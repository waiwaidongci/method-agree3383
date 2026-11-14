from sqlalchemy import select
from src.config.database import Session
from src.models import ComparisonRun,RecalculationBatch,RunVersion
from src.constants.enums import BatchStatus,ItemStatus
from src.services import run_service

def test_resume_after_interrupted_batch(client,tokens):
    """worker中断后重新推进：已就绪项不重算，剩余项续算至整批就绪。"""
    r=client.post("/runs",headers=tokens["analyst"],json={"name":"续算研究","dataset_id":1,"lambda_ratio":1,"allowance":2,"range_low":0,"range_high":100,"bootstrap_count":200})
    rid=r.json()["id"]
    with Session() as db:run_service.execute_run(db,rid)
    r=client.post("/recalculations",headers=tokens["analyst"],json={"run_ids":[rid],"lambda_ratio":0.5})
    bid=r.json()["id"]
    with Session() as db:
        item=run_service.list_batch_items(db,bid)[0]
        item.status=ItemStatus.RUNNING;db.commit()   # 模拟中断时残留 running
    with Session() as db:run_service.execute_batch(db,bid)
    with Session() as db:
        batch=db.get(RecalculationBatch,bid);item=run_service.list_batch_items(db,bid)[0]
        assert batch.status==BatchStatus.READY and item.status==ItemStatus.READY
    assert client.post(f"/recalculations/{bid}/activate",headers=tokens["analyst"]).status_code==200
    with Session() as db:
        run=db.get(ComparisonRun,rid)
        assert run.result["config"]["lambda_ratio"]==0.5
