import itertools,json
import pytest
_NAMES=itertools.count()
from sqlalchemy import select
from src.config.database import Session
from src.models import ComparisonRun,RunVersion,RecalculationBatch,RecalculationItem,Qualification
from src.constants.enums import BatchStatus,ItemStatus,VersionStatus,VersionSource
from src.services import run_service

def _run_ids(db):
    return [r.id for r in db.scalars(select(ComparisonRun).order_by(ComparisonRun.id)).all()]

def _create_runs(client,tokens,n=3):
    ids=[]
    with Session() as db:datasets=[d.id for d in db.scalars(select(__import__('src.models',fromlist=['PairDataset']).PairDataset).order_by(__import__('src.models',fromlist=['PairDataset']).PairDataset.id)).all()]
    for i in range(n):
        r=client.post("/runs",headers=tokens["analyst"],json={"name":f"重算测试 {next(_NAMES)}","dataset_id":datasets[i%len(datasets)],"lambda_ratio":1,"allowance":2,"range_low":0,"range_high":100,"bootstrap_count":200})
        assert r.status_code==201,r.text
        ids.append(r.json()["id"])
    with Session() as db:
        for rid in ids:run_service.execute_run(db,rid)
    return ids

def _batch(client,tokens,run_ids,lam,note=""):
    return client.post("/recalculations",headers=tokens["analyst"],json={"run_ids":run_ids,"lambda_ratio":lam,"note":note})

def test_original_version_survives_and_new_batch_not_active_until_activate(client,tokens):
    rid=_create_runs(client,tokens,1)[0]
    with Session() as db:
        run=db.get(ComparisonRun,rid);orig_fp=run.result["fingerprint"];orig_lam=run.result["config"]["lambda_ratio"]
        assert run.active_version_id and db.get(RunVersion,run.active_version_id).version_no==1
    r=_batch(client,tokens,[rid],0.5);assert r.status_code==201,r.text
    batch=r.json()
    with Session() as db:run_service.execute_batch(db,batch["id"])
    with Session() as db:
        run=db.get(ComparisonRun,rid)
        assert run.status=="completed"
        assert run.result["fingerprint"]==orig_fp and run.result["config"]["lambda_ratio"]==orig_lam, "启用前必须仍用旧结果"
        assert run.lambda_ratio==orig_lam
        v=db.get(RunVersion,run.active_version_id);assert v.version_no==1
        candidates=[x for x in db.scalars(select(RunVersion).where(RunVersion.run_id==rid)).all() if x.source=="recalc"]
        assert len(candidates)==1 and candidates[0].result["config"]["lambda_ratio"]==0.5
        assert candidates[0].status==VersionStatus.CANDIDATE
    r=client.post(f"/recalculations/{batch['id']}/activate",headers=tokens["analyst"]);assert r.status_code==200,r.text
    with Session() as db:
        run=db.get(ComparisonRun,rid)
        assert run.result["config"]["lambda_ratio"]==0.5 and run.lambda_ratio==0.5
        versions=db.scalars(select(RunVersion).where(RunVersion.run_id==rid).order_by(RunVersion.version_no)).all()
        assert [v.status for v in versions]==[VersionStatus.SUPERSEDED,VersionStatus.ACTIVE]
        assert versions[0].result["fingerprint"]==orig_fp, "原版结果留给资格档案复算"

def test_batch_is_atomic_ready_and_activation_requires_all_items(client,tokens):
    ids=_create_runs(client,tokens,3)
    r=_batch(client,tokens,ids,0.7);assert r.status_code==201
    bid=r.json()["id"];assert r.json()["reused"] is False
    with Session() as db:run_service.execute_batch(db,bid)
    with Session() as db:
        batch=db.get(RecalculationBatch,bid);items=run_service.list_batch_items(db,bid)
        assert batch.status==BatchStatus.READY and all(i.status==ItemStatus.READY for i in items)
        for i in items:
            run=db.get(ComparisonRun,i.run_id)
            assert run.active_version_id==i.base_version_id, "整批启用前不得逐项生效"
    assert client.post(f"/recalculations/{bid}/activate",headers=tokens["analyst"]).status_code==200
    # 人工模拟一项失败 -> 整批不进 READY，任何研究不得切换
    r2=_batch(client,tokens,ids,0.8);bid2=r2.json()["id"]
    with Session() as db:
        item=run_service.list_batch_items(db,bid2)[0]
        item.status=ItemStatus.FAILED;item.error="模拟失败";db.commit()
        run_service.execute_batch(db,bid2)
    with Session() as db:
        assert db.get(RecalculationBatch,bid2).status==BatchStatus.FAILED
        for i in run_service.list_batch_items(db,bid2):
            run=db.get(ComparisonRun,i.run_id);assert run.active_version_id==i.base_version_id
    r=client.post(f"/recalculations/{bid2}/activate",headers=tokens["analyst"]);assert r.status_code==409

def test_duplicate_batch_request_returns_same_batch(client,tokens):
    ids=_create_runs(client,tokens,2)
    a=_batch(client,tokens,ids,0.6);b=_batch(client,tokens,ids,0.6)
    assert a.status_code==201 and b.status_code==201
    assert a.json()["id"]==b.json()["id"] and b.json()["reused"] is True
    with Session() as db:
        assert db.scalar(select(RecalculationBatch).where(RecalculationBatch.id==a.json()["id"])) is not None
        batches=db.scalars(select(RecalculationBatch)).all()
        assert len(batches)==1
        assert len(db.scalars(select(RunVersion).where(RunVersion.run_id.in_(ids),RunVersion.source==VersionSource.RECALC)).all())==2, "同批重复发起不重建研究"

def test_conflict_then_revoke_allows_new_batch_and_late_result_cannot_overwrite(client,tokens):
    ids=_create_runs(client,tokens,1);rid=ids[0]
    a=_batch(client,tokens,[rid],0.4);assert a.status_code==201
    conflict=_batch(client,tokens,[rid],0.3);assert conflict.status_code==409
    with Session() as db:run_service.execute_batch(db,a.json()["id"])
    assert client.post(f"/recalculations/{a.json()['id']}/revoke",headers=tokens["analyst"]).status_code==200
    b=_batch(client,tokens,[rid],0.3);assert b.status_code==201,b.text
    # 撤销批的某条目试图晚到写回：代次已变，不得覆盖接替批
    with Session() as db:
        old_item=run_service.list_batch_items(db,a.json()["id"])[0]
        old_version=db.get(RunVersion,old_item.version_id)
        late={"fingerprint":"late-batch","intercept":0,"slope":1,"config":run_service.version_config(old_version),"raw_eligible":False,"points":[],"x_range":[0,1],"y_range":[0,1],"lower_ci":[0,0],"upper_ci":[0,0],"heteroscedastic_warning":False,"bias":0,"loa_lower":0,"loa_upper":0,"sd_difference":0,"correlation":0,"spread_ratio":1,"bootstrap_success":1,"bootstrap_failed":0,"bootstrap_seed":73,"intercept_ci":[0,0],"slope_ci":[1,1],"bias_ci":[0,0]}
        assert run_service._write_item_result(db,old_item,result=late) is False
        assert db.get(RunVersion,old_item.version_id).result!={"fingerprint":"late-batch"} and db.get(RunVersion,old_item.version_id).result.get("fingerprint")!="late-batch"
    with Session() as db:run_service.execute_batch(db,b.json()["id"])
    with Session() as db:
        run=db.get(ComparisonRun,rid)
        assert run.active_version_id!=old_item.version_id
        b_items=run_service.list_batch_items(db,b.json()["id"])
        run_service.execute_batch(db,a.json()["id"])  # 已撤销批次重跑也是空操作
    assert client.post(f"/recalculations/{b.json()['id']}/activate",headers=tokens["analyst"]).status_code==200
    with Session() as db:
        run=db.get(ComparisonRun,rid)
        assert run.result["config"]["lambda_ratio"]==0.3
        assert run.active_version_id==run_service.list_batch_items(db,b.json()["id"])[0].version_id

def test_qualified_record_keeps_original_version_and_exports_original(client,tokens):
    rid=_create_runs(client,tokens,1)[0]
    with Session() as db:
        run=db.get(ComparisonRun,rid);xr=run.result["x_range"]
    payload={"title":f"资格版本固定 {rid}","run_id":rid,"mode":"calibration_only","scope_low":round(xr[0]+0.01,3),"scope_high":round(xr[1]-0.01,3),"note":"固定引用创建时版本，用于复算验证。"}
    r=client.post("/qualifications",headers=tokens["analyst"],json=payload);assert r.status_code==201,r.text
    qid=r.json()["id"]
    orig_fp=json.loads(json.dumps(r.json()))["version_id"]
    client.patch(f"/qualifications/{qid}/approve",headers=tokens["admin"])
    before=client.get(f"/qualifications/{qid}/export",headers=tokens["viewer"]).text
    bid=_batch(client,tokens,[rid],0.55).json()["id"]
    with Session() as db:run_service.execute_batch(db,bid)
    client.post(f"/recalculations/{bid}/activate",headers=tokens["analyst"])
    after=client.get(f"/qualifications/{qid}/export",headers=tokens["viewer"]).text
    assert before==after, "已批准资格仍按原版导出"
    with Session() as db:
        q=db.get(Qualification,qid);assert q.version_id==orig_fp
        run=db.get(ComparisonRun,rid);assert run.active_version_id!=q.version_id
    # 试算可显式引用原版本，即使当前启用版是重算版
    with Session() as db:
        q=db.get(Qualification,qid)
    r=client.post(f"/runs/{rid}/calibrate",headers=tokens["viewer"],json={"candidate_value":50,"version_id":q.version_id})
    assert r.status_code==200 and r.json()["version_id"]==orig_fp
    r=client.post(f"/runs/{rid}/calibrate",headers=tokens["viewer"],json={"candidate_value":50})
    assert r.status_code==200 and r.json()["version_id"]!=orig_fp

def test_failed_recalculation_keeps_old_result(client,tokens):
    rid=_create_runs(client,tokens,1)[0]
    with Session() as db:before=db.get(ComparisonRun,rid).result["fingerprint"]
    # 退化输入：范围只留极少数点 -> 计算失败
    payload={"run_ids":[rid],"lambda_ratio":0.5}
    r=_batch(client,tokens,[rid],0.5);bid=r.json()["id"]
    with Session() as db:
        item=run_service.list_batch_items(db,bid)[0]
        v=db.get(RunVersion,item.version_id);v.range_low=99.999;v.range_high=100;db.commit()
    with Session() as db:run_service.execute_batch(db,bid)
    with Session() as db:
        batch=db.get(RecalculationBatch,bid);assert batch.status==BatchStatus.FAILED
        run=db.get(ComparisonRun,rid);assert run.status=="completed" and run.result["fingerprint"]==before
    assert client.post(f"/recalculations/{bid}/activate",headers=tokens["analyst"]).status_code==409

def test_permissions_on_recalculation(client,tokens):
    rid=_create_runs(client,tokens,1)[0]
    r=client.post("/recalculations",headers=tokens["viewer"],json={"run_ids":[rid],"lambda_ratio":0.5})
    assert r.status_code==403
    bid=_batch(client,tokens,[rid],0.5).json()["id"]
    with Session() as db:run_service.execute_batch(db,bid)
    assert client.post(f"/recalculations/{bid}/activate",headers=tokens["viewer"]).status_code==403
    assert client.post(f"/recalculations/{bid}/revoke",headers=tokens["viewer"]).status_code==403
    assert client.get("/recalculations",headers=tokens["viewer"]).status_code==200

def test_versions_directory_and_calibration_reference_full_config(client,tokens):
    rid=_create_runs(client,tokens,1)[0]
    bid=_batch(client,tokens,[rid],0.45).json()["id"]
    with Session() as db:run_service.execute_batch(db,bid)
    assert client.post(f"/recalculations/{bid}/activate",headers=tokens["analyst"]).status_code==200
    versions=client.get(f"/runs/{rid}/versions",headers=tokens["viewer"]).json()
    assert [v["version_no"] for v in versions]==[1,2]
    assert versions[0]["status"]=="superseded" and versions[1]["status"]=="active"
    for v in versions:
        assert set(v["config"])=={"dataset_id","lambda_ratio","allowance","range_low","range_high","bootstrap_count"}
        assert v["result"]["fingerprint"] and "points" not in v["result"], "版本目录引用完整参数与样本依据，不内联逐点"
    old=client.post(f"/runs/{rid}/calibrate",headers=tokens["viewer"],json={"candidate_value":50,"version_id":versions[0]["id"]}).json()
    new=client.post(f"/runs/{rid}/calibrate",headers=tokens["viewer"],json={"candidate_value":50,"version_id":versions[1]["id"]}).json()
    assert old["config"]["lambda_ratio"]==1 and new["config"]["lambda_ratio"]==0.45
    assert old["fingerprint"]!=new["fingerprint"]

def test_new_qualification_can_reference_superseded_version(client,tokens):
    rid=_create_runs(client,tokens,1)[0]
    with Session() as db:orig=db.get(ComparisonRun,rid).active_version_id;xr=db.get(ComparisonRun,rid).result["x_range"]
    bid=_batch(client,tokens,[rid],0.65).json()["id"]
    with Session() as db:run_service.execute_batch(db,bid)
    client.post(f"/recalculations/{bid}/activate",headers=tokens["analyst"])
    active=client.get(f"/runs/{rid}/versions",headers=tokens["viewer"]).json()[-1]["id"]
    assert active!=orig
    payload={"title":"引用旧版参数的新资格","run_id":rid,"version_id":orig,"mode":"calibration_only","scope_low":round(xr[0]+0.01,3),"scope_high":round(xr[1]-0.01,3),"note":"依据重算前的完整一版参数与样本依据。"}
    r=client.post("/qualifications",headers=tokens["analyst"],json=payload)
    assert r.status_code==201,r.text
    assert r.json()["version_id"]==orig
    # 未指定版本时默认引用当前启用版本
    payload2=dict(payload,title="引用当前启用版本",version_id=None)
    r2=client.post("/qualifications",headers=tokens["analyst"],json=payload2)
    assert r2.status_code==201 and r2.json()["version_id"]==active

def test_second_batch_after_activation_and_versions_keep_ordered(client,tokens):
    rid=_create_runs(client,tokens,1)[0]
    for lam in (0.8,0.6,0.4):
        r=_batch(client,tokens,[rid],lam);assert r.status_code==201,r.text
        bid=r.json()["id"]
        with Session() as db:run_service.execute_batch(db,bid)
        assert client.post(f"/recalculations/{bid}/activate",headers=tokens["analyst"]).status_code==200
    versions=client.get(f"/runs/{rid}/versions",headers=tokens["viewer"]).json()
    assert [v["version_no"] for v in versions]==[1,2,3,4]
    assert [v["status"] for v in versions]==["superseded"]*3+["active"]
    assert [v["config"]["lambda_ratio"] for v in versions]==[1.0,0.8,0.6,0.4]
    assert len({v["result"]["fingerprint"] for v in versions})==4, "每一版指纹都由各自的完整参数与样本决定"
    # 已归档版本仍可被试算引用
    r=client.post(f"/runs/{rid}/calibrate",headers=tokens["viewer"],json={"candidate_value":50,"version_id":versions[0]["id"]})
    assert r.status_code==200 and r.json()["version_no"]==1
