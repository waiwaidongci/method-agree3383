import csv,io
from sqlalchemy import select
from fastapi import HTTPException
from src.models import Qualification,ComparisonRun,RunVersion
from src.constants.enums import RunStatus,VersionStatus,QualificationMode,QualificationStatus
from src.services.result_service import active_result
def list_qualifications(db):return db.scalars(select(Qualification).order_by(Qualification.id.desc())).all()
def qualification_version(db,item):
    """资格档案固定引用创建时的版本；老记录缺 version_id 时回落到原始 v1。"""
    if item.version_id:
        version=db.get(RunVersion,item.version_id)
        if version:return version
    run=db.get(ComparisonRun,item.run_id)
    if not run:raise HTTPException(404,"研究不存在")
    version=db.scalar(select(RunVersion).where(RunVersion.run_id==run.id,RunVersion.version_no==1))
    if version:return version
    if run.status!=RunStatus.COMPLETED:raise HTTPException(409,"研究尚未完成")
    return None
def _result_for(db,item):
    version=qualification_version(db,item)
    if version is None:return None,db.get(ComparisonRun,item.run_id).result
    if not version.result:raise HTTPException(409,"资格所依据的研究版本尚未完成计算")
    return version,version.result
def validate_scope(run,result,mode,low,high):
    if low<result['x_range'][0]-1e-9 or high>result['x_range'][1]+1e-9:raise HTTPException(422,"资格范围必须包含在本次实际观测X范围内")
    if mode==QualificationMode.RAW and not result['raw_eligible']:raise HTTPException(422,"原值互换条件未满足：需外侧bootstrap界限在允许误差内，且无异方差提示")
def create_qualification(db,data):
    run=db.get(ComparisonRun,data.run_id)
    if not run:raise HTTPException(404,"研究不存在")
    version,result=active_result(db,run,data.version_id)
    if version is None:raise HTTPException(409,"研究尚未完成")
    validate_scope(run,result,data.mode,data.scope_low,data.scope_high)
    if db.scalar(select(Qualification).where(Qualification.title==data.title)):raise HTTPException(409,"资格档案标题已存在")
    item=Qualification(**data.model_dump(exclude={"version_id"}),version_id=version.id,status=QualificationStatus.DRAFT);db.add(item);db.commit();return item
def approve_qualification(db,id,user):
    item=db.get(Qualification,id)
    if not item:raise HTTPException(404,"资格档案不存在")
    run=db.get(ComparisonRun,item.run_id)
    if not run:raise HTTPException(404,"研究不存在")
    version,result=_result_for(db,item)
    validate_scope(run,result,item.mode,item.scope_low,item.scope_high)
    if item.version_id is None and version is not None:item.version_id=version.id
    item.status=QualificationStatus.APPROVED;item.approved_by=user.email;db.commit();return item
def export_qualification(db,id):
    item=db.get(Qualification,id)
    if not item:raise HTTPException(404,"资格档案不存在")
    if item.status!=QualificationStatus.APPROVED:raise HTTPException(409,"先由负责人批准资格档案")
    version,result=_result_for(db,item)
    if 'points' not in result:raise HTTPException(409,"该版研究结果缺少逐点输入，无法导出；请让负责人按原版重算资格")
    output=io.StringIO();output.write("﻿");writer=csv.writer(output)
    writer.writerow(['sample_code','reference_pct','candidate_pct','difference_pct','inverse_calibrated_pct','mode','scope_low','scope_high','run_fingerprint','run_version'])
    for point in result['points']:
        if item.scope_low<=point['x']<=item.scope_high:
            code=point['code'];code=("'"+code) if code.startswith(('=','+','-','@','\t','\r')) else code
            writer.writerow([code,point['x'],point['y'],point['diff'],(point['y']-result['intercept'])/result['slope'],item.mode,item.scope_low,item.scope_high,result['fingerprint'],f"v{version.version_no if version else 1}"])
    return output.getvalue()
