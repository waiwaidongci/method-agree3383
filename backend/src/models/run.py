from datetime import datetime
from sqlalchemy import String,ForeignKey,Float,Integer,JSON,DateTime,UniqueConstraint
from sqlalchemy.orm import Mapped,mapped_column
from src.config.database import Base
class ComparisonRun(Base):
    __tablename__="comparison_runs"
    id:Mapped[int]=mapped_column(primary_key=True)
    name:Mapped[str]=mapped_column(String(160),unique=True)
    dataset_id:Mapped[int]=mapped_column(ForeignKey("pair_datasets.id"))
    lambda_ratio:Mapped[float]=mapped_column(Float)
    allowance:Mapped[float]=mapped_column(Float)
    range_low:Mapped[float]=mapped_column(Float)
    range_high:Mapped[float]=mapped_column(Float)
    bootstrap_count:Mapped[int]=mapped_column(default=500)
    status:Mapped[str]=mapped_column(String(20),default="queued")
    error:Mapped[str]=mapped_column(String(600),default="")
    result:Mapped[dict]=mapped_column(JSON,default=dict)
    # 当前启用的版本；重算只切换指针，原版本与资格引用均保留。
    active_version_id:Mapped[int|None]=mapped_column(Integer,nullable=True)
    # 每次有新批次占用该研究时递增，用于拒绝晚到的旧批次结果。
    recalc_generation:Mapped[int]=mapped_column(Integer,default=0)
    created_at:Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow)
class RunVersion(Base):
    __tablename__="run_versions"
    __table_args__=(UniqueConstraint("run_id","version_no"),)
    id:Mapped[int]=mapped_column(primary_key=True)
    run_id:Mapped[int]=mapped_column(ForeignKey("comparison_runs.id"))
    version_no:Mapped[int]=mapped_column(Integer)
    source:Mapped[str]=mapped_column(String(20),default="original")
    status:Mapped[str]=mapped_column(String(20),default="candidate")
    batch_id:Mapped[int|None]=mapped_column(ForeignKey("recalculation_batches.id"),nullable=True)
    generation:Mapped[int]=mapped_column(Integer,default=0)
    dataset_id:Mapped[int]=mapped_column(ForeignKey("pair_datasets.id"))
    lambda_ratio:Mapped[float]=mapped_column(Float)
    allowance:Mapped[float]=mapped_column(Float)
    range_low:Mapped[float]=mapped_column(Float)
    range_high:Mapped[float]=mapped_column(Float)
    bootstrap_count:Mapped[int]=mapped_column(default=500)
    result:Mapped[dict]=mapped_column(JSON,default=dict)
    error:Mapped[str]=mapped_column(String(600),default="")
    created_at:Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow)
    activated_at:Mapped[datetime|None]=mapped_column(DateTime,nullable=True)
class RecalculationBatch(Base):
    __tablename__="recalculation_batches"
    id:Mapped[int]=mapped_column(primary_key=True)
    request_key:Mapped[str]=mapped_column(String(64),index=True)
    lambda_ratio:Mapped[float]=mapped_column(Float)
    note:Mapped[str]=mapped_column(String(600),default="")
    created_by:Mapped[str]=mapped_column(String(160),default="")
    status:Mapped[str]=mapped_column(String(20),default="queued")
    error:Mapped[str]=mapped_column(String(600),default="")
    created_at:Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow)
    started_at:Mapped[datetime|None]=mapped_column(DateTime,nullable=True)
    finished_at:Mapped[datetime|None]=mapped_column(DateTime,nullable=True)
    activated_at:Mapped[datetime|None]=mapped_column(DateTime,nullable=True)
    revoked_at:Mapped[datetime|None]=mapped_column(DateTime,nullable=True)
class RecalculationItem(Base):
    __tablename__="recalculation_items"
    __table_args__=(UniqueConstraint("batch_id","run_id"),)
    id:Mapped[int]=mapped_column(primary_key=True)
    batch_id:Mapped[int]=mapped_column(ForeignKey("recalculation_batches.id"))
    run_id:Mapped[int]=mapped_column(ForeignKey("comparison_runs.id"))
    base_version_id:Mapped[int]=mapped_column(ForeignKey("run_versions.id"))
    version_id:Mapped[int]=mapped_column(ForeignKey("run_versions.id"))
    generation:Mapped[int]=mapped_column(Integer,default=0)
    status:Mapped[str]=mapped_column(String(20),default="queued")
    error:Mapped[str]=mapped_column(String(600),default="")
    created_at:Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow)
    updated_at:Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow)
