import hashlib,json
from datetime import datetime
from sqlalchemy import select,func
from fastapi import HTTPException
from redis import Redis
from src.config.settings import REDIS_URL
from src.constants.enums import RunStatus,VersionSource,VersionStatus,BatchStatus,ItemStatus
from src.models import PairDataset,PairedReading,Instrument,ComparisonRun,RunVersion,RecalculationBatch,RecalculationItem
from src.services.agreement import agreement_analysis,ALGORITHM_VERSION
from src.services.deming import inverse_calibrate
queue=Redis.from_url(REDIS_URL,socket_connect_timeout=1,socket_timeout=1)
def _wake(token):
    # DB状态是事实源；Redis仅唤醒worker，不可用时worker仍会轮询数据库。
    try:queue.lpush("optics:runs",token)
    except Exception:pass
CONFIG_FIELDS=('dataset_id','lambda_ratio','allowance','range_low','range_high','bootstrap_count')
OPEN_BATCH_STATUSES=(BatchStatus.QUEUED,BatchStatus.RUNNING,BatchStatus.READY)
LIVE_ITEM_STATUSES=(ItemStatus.QUEUED,ItemStatus.RUNNING,ItemStatus.READY)

def version_config(version):
    return {key:getattr(version,key) for key in CONFIG_FIELDS}

def compute_version(db,version,config=None):
    """按给定的一版参数（数据集、范围、允许误差、λ、bootstrap）完整重算，返回可归档结果。"""
    config=dict(config or version_config(version))
    dataset=db.get(PairDataset,config['dataset_id'])
    if not dataset:raise ValueError("数据集不存在")
    rows=db.scalars(select(PairedReading).where(PairedReading.dataset_id==dataset.id,PairedReading.reference_value>=config['range_low'],PairedReading.reference_value<=config['range_high']).order_by(PairedReading.sample_code)).all()
    x=[r.reference_value for r in rows];y=[r.candidate_value for r in rows]
    result=agreement_analysis(x,y,config['lambda_ratio'],config['bootstrap_count'])
    instruments=[db.get(Instrument,dataset.reference_id),db.get(Instrument,dataset.candidate_id)]
    snapshot={"algorithm":ALGORITHM_VERSION,"dataset":{"id":dataset.id,"name":dataset.name,"source_file":dataset.source_file},"instruments":[{"id":i.id,"code":i.code,"unit":i.unit,"lower":i.lower,"upper":i.upper} for i in instruments],"pairs":[[r.sample_code,r.reference_value,r.candidate_value] for r in rows],"config":config,"seed":73}
    result.update({"fingerprint":hashlib.sha256(json.dumps(snapshot,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest(),"x_range":[min(x),max(x)],"y_range":[min(y),max(y)],"config":config,"instrument_codes":[i.code for i in instruments],"raw_eligible":result['lower_ci'][0]>=-config['allowance'] and result['upper_ci'][1]<=config['allowance'] and not result['heteroscedastic_warning'],"points":[{"code":r.sample_code,"x":r.reference_value,"y":r.candidate_value,"mean":(r.reference_value+r.candidate_value)/2,"diff":r.candidate_value-r.reference_value} for r in rows]})
    return result

def _new_version(db,run,version_no,source,config,status=VersionStatus.CANDIDATE,batch_id=None,generation=0):
    version=RunVersion(run_id=run.id,version_no=version_no,source=source,status=status,batch_id=batch_id,generation=generation,result={},error="",**config)
    db.add(version);db.flush();return version

def _sync_active(run,version):
    run.active_version_id=version.id
    run.result=version.result or {}
    run.error=version.error or ""
    run.status=RunStatus.FAILED if version.error else RunStatus.COMPLETED
    run.lambda_ratio=version.lambda_ratio

def active_version(db,run,create=False):
    """研究当前启用版本；旧研究若还没有版本行，用归档的原始结果补建 v1。"""
    if run.active_version_id:
        version=db.get(RunVersion,run.active_version_id)
        if version:return version
    version=db.scalar(select(RunVersion).where(RunVersion.run_id==run.id,RunVersion.status==VersionStatus.ACTIVE).order_by(RunVersion.version_no.desc()))
    if version:return version
    version=db.scalar(select(RunVersion).where(RunVersion.run_id==run.id,RunVersion.version_no==1))
    if version or not create:return version
    if run.status not in (RunStatus.COMPLETED,RunStatus.FAILED):return None  # 初始化中的研究由worker生成 v1
    config={key:getattr(run,key) for key in CONFIG_FIELDS}
    version=_new_version(db,run,1,VersionSource.ORIGINAL,config,status=VersionStatus.ACTIVE,generation=run.recalc_generation or 0)
    version.result=run.result or {};version.error=run.error or ""
    db.flush();run.active_version_id=version.id;db.commit();db.refresh(version)
    return version

def ensure_versions(db):
    """为旧研究补建/挂接原始 v1，保证任何结果都有可引用的版本行。"""
    for run in db.scalars(select(ComparisonRun).where(ComparisonRun.active_version_id.is_(None),ComparisonRun.status.in_([RunStatus.COMPLETED,RunStatus.FAILED]))).all():
        try:
            version=active_version(db,run,create=True)
            if version and run.active_version_id!=version.id:
                run.active_version_id=version.id;db.commit()
        except Exception:db.rollback()

def serialized_version(version,include_points=False):
    payload={"id":version.id,"run_id":version.run_id,"version_no":version.version_no,"source":version.source,"status":version.status,"batch_id":version.batch_id,"generation":version.generation,"config":version_config(version),"allowance":version.allowance,"error":version.error,"created_at":version.created_at,"activated_at":version.activated_at,"result":{k:v for k,v in (version.result or {}).items() if include_points or k!="points"}}
    return payload

def get_version(db,run_id,version_id,include_points=False):
    get_run(db,run_id)
    version=db.get(RunVersion,version_id)
    if not version or version.run_id!=run_id:raise HTTPException(404,"研究版本不存在")
    return serialized_version(version,include_points=include_points)

def list_versions(db,run_id):
    """版本目录：含完整参数与结果摘要（不含逐点数据），供试算与新资格显式引用。run_id 传 0 表示全部研究。"""
    if run_id:get_run(db,run_id)
    stmt=select(RunVersion).order_by(RunVersion.run_id,RunVersion.version_no)
    if run_id:stmt=stmt.where(RunVersion.run_id==run_id)
    return [serialized_version(v) for v in db.scalars(stmt).all()]

# ---------- 研究 ----------
def _pending_item(db,run_id):
    return db.scalar(select(RecalculationItem).join(RecalculationBatch,RecalculationBatch.id==RecalculationItem.batch_id).where(RecalculationItem.run_id==run_id,RecalculationBatch.status.in_(OPEN_BATCH_STATUSES),RecalculationItem.status.in_(LIVE_ITEM_STATUSES)).order_by(RecalculationItem.id.desc()))

def serialize_run(db,run):
    version=db.get(RunVersion,run.active_version_id) if run.active_version_id else None
    pending=_pending_item(db,run.id)
    return {"id":run.id,"name":run.name,"dataset_id":run.dataset_id,"lambda_ratio":run.lambda_ratio,"allowance":run.allowance,"range_low":run.range_low,"range_high":run.range_high,"bootstrap_count":run.bootstrap_count,"status":run.status,"error":run.error,"created_at":run.created_at,"result":run.result or {},"active_version_id":run.active_version_id,"active_version_no":version.version_no if version else None,"active_version_source":version.source if version else None,"active_version_status":version.status if version else None,"pending_batch_id":pending.batch_id if pending else None,"pending_batch_status":db.get(RecalculationBatch,pending.batch_id).status if pending else None}

def list_runs(db):return [serialize_run(db,run) for run in db.scalars(select(ComparisonRun).order_by(ComparisonRun.id.desc())).all()]

def get_run(db,id):
    run=db.get(ComparisonRun,id)
    if not run:raise HTTPException(404,"比对研究不存在")
    return run

def run_view(db,id):return serialize_run(db,get_run(db,id))

def create_run(db,data):
    dataset=db.get(PairDataset,data.dataset_id)
    if not dataset:raise HTTPException(404,"数据集不存在")
    if db.scalar(select(ComparisonRun).where(ComparisonRun.name==data.name)):raise HTTPException(409,"研究名称已存在")
    config={key:getattr(data,key) for key in CONFIG_FIELDS}
    run=ComparisonRun(name=data.name,status=RunStatus.QUEUED,**config);db.add(run);db.commit()
    _new_version(db,run,1,VersionSource.ORIGINAL,config,status=VersionStatus.PENDING,generation=run.recalc_generation or 0)
    db.commit();_wake(f"run:{run.id}");return run

def execute_run(db,id):
    """计算初始 v1。若该研究已被重算批次接替，只完成版本行，不切换启用指针。"""
    run=db.get(ComparisonRun,id)
    if not run or run.status not in (RunStatus.QUEUED,RunStatus.RUNNING):return
    version=db.scalar(select(RunVersion).where(RunVersion.run_id==run.id,RunVersion.version_no==1))
    if version and version.result and version.status in (VersionStatus.ACTIVE,VersionStatus.CANDIDATE,VersionStatus.SUPERSEDED):return
    run.status=RunStatus.RUNNING;db.commit()
    if version is None:version=_new_version(db,run,1,VersionSource.ORIGINAL,{key:getattr(run,key) for key in CONFIG_FIELDS},status=VersionStatus.PENDING,generation=run.recalc_generation or 0);db.commit()
    try:
        result=compute_version(db,version)
        db.refresh(run);db.refresh(version)
        version.result=result;version.error=""
        if (run.recalc_generation or 0)!=version.generation:
            version.status=VersionStatus.DISCARDED;version.error="研究已被后续重算批次接替"
        else:
            version.status=VersionStatus.ACTIVE;version.activated_at=datetime.utcnow();_sync_active(run,version)
        db.commit()
    except Exception as exc:
        db.rollback();run=db.get(ComparisonRun,id)
        version=db.scalar(select(RunVersion).where(RunVersion.run_id==run.id,RunVersion.version_no==1))
        if version:version.error=str(exc)[:600]
        run.status=RunStatus.FAILED;run.error=str(exc)[:600]
        db.commit()

def calibrate(db,id,value,version_id=None):
    run=get_run(db,id)
    version=db.get(RunVersion,version_id) if version_id else active_version(db,run)
    if not version or version.run_id!=run.id:raise HTTPException(404,"研究版本不存在")
    if not version.result or 'slope' not in version.result:raise HTTPException(409,"所选版本尚未完成计算")
    result=version.result
    try:estimate=inverse_calibrate(value,result['intercept'],result['slope'],result['x_range'],result['y_range'])
    except ValueError as exc:raise HTTPException(422,str(exc))
    return {"reference_estimate":estimate,"candidate_value":value,"run_id":run.id,"version_id":version.id,"version_no":version.version_no,"fingerprint":result.get('fingerprint'),"config":result.get('config'),"scope":"研究内逆校准点估计，不含个体预测误差"}

# ---------- 重算批次 ----------
def request_fingerprint(items,lambda_ratio):
    payload={"lambda_ratio":lambda_ratio,"runs":sorted([[run_id,base_id] for run_id,base_id in items])}
    return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':')).encode()).hexdigest()

def list_batches(db):
    return [serialize_batch(db,b) for b in db.scalars(select(RecalculationBatch).order_by(RecalculationBatch.id.desc())).all()]

def list_batch_items(db,batch_id):
    return db.scalars(select(RecalculationItem).where(RecalculationItem.batch_id==batch_id).order_by(RecalculationItem.id)).all()

def get_batch(db,batch_id):
    batch=db.get(RecalculationBatch,batch_id)
    if not batch:raise HTTPException(404,"重算批次不存在")
    return serialize_batch(db,batch)

def _batch_row(db,batch_id):
    batch=db.get(RecalculationBatch,batch_id)
    if not batch:raise HTTPException(404,"重算批次不存在")
    return batch

def _claimed_run_ids(db):
    rows=db.execute(select(RecalculationItem.run_id).join(RecalculationBatch,RecalculationBatch.id==RecalculationItem.batch_id).where(RecalculationBatch.status.in_(OPEN_BATCH_STATUSES),RecalculationItem.status.in_(LIVE_ITEM_STATUSES))).all()
    return {r[0] for r in rows if r[0]}

def create_recalculation(db,data,user):
    run_ids=sorted(set(data.run_ids))
    if not run_ids:raise HTTPException(422,"请至少选择一个研究")
    bases=[]
    for run_id in run_ids:
        run=get_run(db,run_id)
        if not run.active_version_id and run.status in (RunStatus.QUEUED,RunStatus.RUNNING):raise HTTPException(409,f"研究 #{run.id} 仍在初始计算中，完成后再纳入重算批次")
        version=active_version(db,run,create=True)
        if not version or not version.result:raise HTTPException(409,f"研究 #{run.id} 尚无可用结果")
        bases.append((run,version))
    request_key=request_fingerprint([[run.id,version.id] for run,version in bases],data.lambda_ratio)
    # 锁住所选研究行：并发发起时串行判定，避免两个批次同时占用同一研究。
    db.execute(select(ComparisonRun.id).where(ComparisonRun.id.in_(run_ids)).with_for_update()).all()
    for batch in db.scalars(select(RecalculationBatch).where(RecalculationBatch.status.in_(OPEN_BATCH_STATUSES))).all():
        if batch.request_key==request_key:
            # 同一批请求重复发起：直接返回原批次，同批研究不重建、不重复计算。
            db.commit();return batch,list_batch_items(db,batch.id),True
    claimed=_claimed_run_ids(db)
    conflicts=[run.id for run,_version in bases if run.id in claimed]
    if conflicts:
        db.rollback()
        raise HTTPException(409,"研究 "+", ".join(f"#{i}" for i in conflicts)+" 已被另一重算批次占用；撤销该批或等待整批启用后再发起")
    batch=RecalculationBatch(request_key=request_key,lambda_ratio=data.lambda_ratio,note=data.note,created_by=user.email,status=BatchStatus.QUEUED)
    db.add(batch);db.flush()
    for run,version in bases:
        run.recalc_generation=(run.recalc_generation or 0)+1
        config=version_config(version);config['lambda_ratio']=data.lambda_ratio
        next_no=(db.scalar(select(func.max(RunVersion.version_no)).where(RunVersion.run_id==run.id)) or 0)+1
        if next_no<=version.version_no:next_no=version.version_no+1
        new_version=_new_version(db,run,next_no,VersionSource.RECALC,config,batch_id=batch.id,generation=run.recalc_generation)
        db.add(RecalculationItem(batch_id=batch.id,run_id=run.id,base_version_id=version.id,version_id=new_version.id,generation=run.recalc_generation,status=ItemStatus.QUEUED))
    db.commit();_wake(f"batch:{batch.id}")
    return batch,list_batch_items(db,batch.id),False

def _finish_batch(db,batch):
    if batch.status in (BatchStatus.REVOKED,BatchStatus.ACTIVATED):return
    items=list_batch_items(db,batch.id)
    if not items:return
    if all(i.status==ItemStatus.READY for i in items):batch.status=BatchStatus.READY;batch.error=""
    elif any(i.status==ItemStatus.FAILED for i in items):
        batch.status=BatchStatus.FAILED;batch.error="至少一份研究计算失败，整批不启用；各研究继续使用原启用版本"
        # 整批不成立：候选版本统一作废，避免日后被误启用。
        for i in items:
            version=db.get(RunVersion,i.version_id)
            if version and version.status==VersionStatus.CANDIDATE:
                version.status=VersionStatus.DISCARDED;version.error="整批计算失败，结果未启用"
    elif all(i.status==ItemStatus.REVOKED for i in items):batch.status=BatchStatus.REVOKED
    else:batch.status=BatchStatus.RUNNING
    if batch.status in (BatchStatus.READY,BatchStatus.FAILED,BatchStatus.REVOKED):batch.finished_at=datetime.utcnow()
    db.commit()

def _write_item_result(db,item,result=None,error=""):
    """短事务 + 行锁校验代次与状态，杜绝撤销/接替后的晚到结果覆盖。"""
    db.rollback()
    run=db.execute(select(ComparisonRun).where(ComparisonRun.id==item.run_id).with_for_update()).scalar_one_or_none()
    batch=db.execute(select(RecalculationBatch).where(RecalculationBatch.id==item.batch_id).with_for_update()).scalar_one()
    fresh=db.get(RecalculationItem,item.id);version=db.get(RunVersion,item.version_id)
    stale=not run or not fresh or not version or batch.status==BatchStatus.REVOKED or fresh.status in (ItemStatus.REVOKED,ItemStatus.READY) or (run.recalc_generation or 0)!=item.generation or version.status!=VersionStatus.CANDIDATE
    if stale:
        if fresh and fresh.status not in (ItemStatus.READY,ItemStatus.REVOKED):
            fresh.status=ItemStatus.REVOKED;fresh.error="批次已被撤销或研究被接替";fresh.updated_at=datetime.utcnow()
        db.commit();return False
    if error:
        version.error=error[:600];fresh.status=ItemStatus.FAILED;fresh.error=error[:600]
    else:
        version.result=result;version.error="";fresh.status=ItemStatus.READY;fresh.error=""
    fresh.updated_at=datetime.utcnow();db.commit();return True

def resume_batches(db):
    """启动恢复：queued/running 批次保持待算，running 项回到 queued 以便续算。"""
    for batch in db.scalars(select(RecalculationBatch).where(RecalculationBatch.status.in_([BatchStatus.QUEUED,BatchStatus.RUNNING]))).all():
        for item in list_batch_items(db,batch.id):
            if item.status==ItemStatus.RUNNING:item.status=ItemStatus.QUEUED;item.updated_at=datetime.utcnow()
    db.commit()

def execute_batch(db,batch_id):
    batch=db.get(RecalculationBatch,batch_id)
    if not batch or batch.status not in (BatchStatus.QUEUED,BatchStatus.RUNNING):return
    batch.status=BatchStatus.RUNNING;batch.started_at=batch.started_at or datetime.utcnow();db.commit()
    for item in list_batch_items(db,batch_id):
        if item.status in (ItemStatus.REVOKED,ItemStatus.READY,ItemStatus.FAILED):continue
        if item.status not in (ItemStatus.QUEUED,ItemStatus.RUNNING):continue
        run=db.get(ComparisonRun,item.run_id)
        if not run or (run.recalc_generation or 0)!=item.generation:
            _write_item_result(db,item,error="批次已被撤销或研究被接替");continue
        item.status=ItemStatus.RUNNING;item.updated_at=datetime.utcnow();db.commit()
        try:
            version=db.get(RunVersion,item.version_id)
            batch=db.get(RecalculationBatch,batch_id)
            config=version_config(version);config['lambda_ratio']=batch.lambda_ratio
            result=compute_version(db,version,config)
            _write_item_result(db,item,result=result)
        except Exception as exc:
            _write_item_result(db,item,error=str(exc))
    db.expire_all();batch=db.get(RecalculationBatch,batch_id);_finish_batch(db,batch)

def revoke_batch(db,batch_id,user):
    batch=_batch_row(db,batch_id)
    if batch.status in (BatchStatus.REVOKED,BatchStatus.ACTIVATED):raise HTTPException(409,"已启用或已撤销的批次不能再次撤销")
    batch.status=BatchStatus.REVOKED;batch.revoked_at=datetime.utcnow();batch.error=f"批次已撤销（{user.email}），结果未启用"
    for item in list_batch_items(db,batch_id):
        if item.status in LIVE_ITEM_STATUSES:
            item.status=ItemStatus.REVOKED;item.error="批次已撤销";item.updated_at=datetime.utcnow()
        version=db.get(RunVersion,item.version_id)
        if version and version.status==VersionStatus.CANDIDATE:version.status=VersionStatus.DISCARDED;version.error="批次已撤销"
        run=db.get(ComparisonRun,item.run_id)
        if run:run.recalc_generation=(run.recalc_generation or 0)+1
    batch.finished_at=batch.revoked_at;db.commit();return batch

def activate_batch(db,batch_id,user):
    batch=_batch_row(db,batch_id)
    if batch.status!=BatchStatus.READY:raise HTTPException(409,"只有整批计算就绪的批次才能统一启用；失败或未完成的批次保留原结果")
    items=list_batch_items(db,batch_id)
    if any(i.status!=ItemStatus.READY for i in items):raise HTTPException(409,"批次中仍有未就绪研究，拒绝启用")
    for item in items:
        run=db.get(ComparisonRun,item.run_id)
        if not run or (run.recalc_generation or 0)!=item.generation:raise HTTPException(409,f"研究 #{item.run_id} 已被后续批次接替，本批结果作废")
        version=db.get(RunVersion,item.version_id);base=db.get(RunVersion,item.base_version_id)
        if not version or version.status!=VersionStatus.CANDIDATE:raise HTTPException(409,f"研究 #{item.run_id} 的候选版本已失效")
        if item.base_version_id and run.active_version_id!=item.base_version_id:raise HTTPException(409,f"研究 #{item.run_id} 的启用版本已变化，本批结果不再适用")
        if base and base.status==VersionStatus.ACTIVE:base.status=VersionStatus.SUPERSEDED
        version.status=VersionStatus.ACTIVE;version.activated_at=datetime.utcnow()
        _sync_active(run,version);run.active_version_id=version.id
    batch.status=BatchStatus.ACTIVATED;batch.activated_at=datetime.utcnow();batch.error="";db.commit();return batch

def serialize_batch(db,batch):
    items=list_batch_items(db,batch.id)
    run_ids=[i.run_id for i in items]
    runs={run.id:run for run in db.scalars(select(ComparisonRun).where(ComparisonRun.id.in_(run_ids or [0]))).all()}
    def item_payload(item):
        base=db.get(RunVersion,item.base_version_id);cand=db.get(RunVersion,item.version_id)
        return {"id":item.id,"batch_id":item.batch_id,"run_id":item.run_id,"run_name":runs[item.run_id].name if item.run_id in runs else "","base_version_id":item.base_version_id,"base_version_no":base.version_no if base else None,"version_id":item.version_id,"version_no":cand.version_no if cand else None,"generation":item.generation,"status":item.status,"error":item.error,"lambda_ratio":cand.lambda_ratio if cand else None,"fingerprint":(cand.result or {}).get("fingerprint") if cand else None,"updated_at":item.updated_at}
    return {"id":batch.id,"request_key":batch.request_key,"lambda_ratio":batch.lambda_ratio,"note":batch.note,"created_by":batch.created_by,"status":batch.status,"error":batch.error,"created_at":batch.created_at,"started_at":batch.started_at,"finished_at":batch.finished_at,"activated_at":batch.activated_at,"revoked_at":batch.revoked_at,"items":[item_payload(i) for i in items]}

