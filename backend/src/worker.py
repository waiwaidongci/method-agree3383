import time
from pathlib import Path
from sqlalchemy import select
from redis import Redis
from src.config.database import Session
from src.config.settings import REDIS_URL
from src.models import ComparisonRun,RecalculationBatch
from src.constants.enums import RunStatus,BatchStatus
from src.services.run_service import execute_run,execute_batch,resume_batches
redis=Redis.from_url(REDIS_URL)
with Session() as db:resume_batches(db)
while True:
    Path('/tmp/heartbeat').touch()
    with Session() as db:
        run=db.scalar(select(ComparisonRun).where(ComparisonRun.status.in_([RunStatus.QUEUED,RunStatus.RUNNING])).order_by(ComparisonRun.id))
        if run:execute_run(db,run.id)
        else:
            batch=db.scalar(select(RecalculationBatch).where(RecalculationBatch.status.in_([BatchStatus.QUEUED,BatchStatus.RUNNING])).order_by(RecalculationBatch.id))
            if batch:execute_batch(db,batch.id)
            else:redis.brpop('optics:runs',timeout=2)
    time.sleep(.1)
