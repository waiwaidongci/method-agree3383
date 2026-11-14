from sqlalchemy import select
from fastapi import HTTPException
from src.models import ComparisonRun,RunVersion
from src.constants.enums import RunStatus,VersionStatus
def active_result(db,run,version_id=None):
    """返回 (version,result)；未指定 version_id 时取启用版本或原始 v1，绝不凭空取“最新计算”。"""
    if version_id is not None:
        version=db.get(RunVersion,version_id)
        if not version or version.run_id!=run.id:raise HTTPException(404,"研究版本不存在")
        if not version.result:raise HTTPException(409,"该研究版本尚未完成计算")
        return version,version.result
    if run.active_version_id:
        version=db.get(RunVersion,run.active_version_id)
        if version and version.result:return version,version.result
    version=db.scalar(select(RunVersion).where(RunVersion.run_id==run.id,RunVersion.status==VersionStatus.ACTIVE).order_by(RunVersion.version_no.desc()))
    if version and version.result:return version,version.result
    if run.status!=RunStatus.COMPLETED:raise HTTPException(409,"研究尚未完成")
    return None,run.result
