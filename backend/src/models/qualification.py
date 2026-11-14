from datetime import datetime
from sqlalchemy import String,ForeignKey,Float,Integer,DateTime
from sqlalchemy.orm import Mapped,mapped_column
from src.config.database import Base
class Qualification(Base):
    __tablename__="qualifications"
    id:Mapped[int]=mapped_column(primary_key=True)
    title:Mapped[str]=mapped_column(String(160),unique=True)
    run_id:Mapped[int]=mapped_column(ForeignKey("comparison_runs.id"))
    # 资格始终引用创建时的那一版研究参数与结果，不随启用的重算版本漂移。
    version_id:Mapped[int|None]=mapped_column(ForeignKey("run_versions.id"),nullable=True)
    mode:Mapped[str]=mapped_column(String(30))
    scope_low:Mapped[float]=mapped_column(Float)
    scope_high:Mapped[float]=mapped_column(Float)
    note:Mapped[str]=mapped_column(String(1000))
    status:Mapped[str]=mapped_column(String(20),default="draft")
    approved_by:Mapped[str]=mapped_column(String(160),default="")
    created_at:Mapped[datetime]=mapped_column(DateTime,default=datetime.utcnow)
