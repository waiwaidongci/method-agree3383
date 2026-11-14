import os,sys,tempfile
os.environ.setdefault("JWT_SECRET","test-secret")
os.environ.setdefault("SEED_DEMO","false")
_db=tempfile.NamedTemporaryFile(suffix=".db",delete=False)
os.environ["DATABASE_URL"]=f"sqlite+pysqlite:///{_db.name}"
os.environ.setdefault("REDIS_URL","redis://localhost:6379/15")
sys.path.insert(0,os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from src.config.database import Base,engine,Session
from src.config.schema_upgrade import ensure_schema
from src.models import User,Instrument,PairDataset,PairedReading
from src.utils.security import hash_password

class _FakeRedis:
    store={}
    def __init__(self,*a,**k):pass
    def incr(self,key):self.store[key]=self.store.get(key,0)+1;return self.store[key]
    def expire(self,key,ttl):return True
    def ping(self):return True
    @classmethod
    def from_url(cls,*a,**k):return cls()

@pytest.fixture(scope="session",autouse=True)
def schema():
    import src.middlewares.rate_limit as rl
    rl.Redis=_FakeRedis
    ensure_schema()
    with Session() as db:
        for role in ("admin","analyst","viewer"):
            db.add(User(email=f"{role}@ciguang.demo",name=role,role=role,password_hash=hash_password("Ciguang2026!")))
        db.add_all([Instrument(code="CG-R01",name="R01",serial="s1"),Instrument(code="CG-N02",name="N02",serial="s2"),Instrument(code="CG-N03",name="N03",serial="s3")])
        db.commit()
        ins=db.query(Instrument).all()
        import random
        rng=random.Random(73)
        for idx,(name,intercept,slope) in enumerate([("N02 稳定光源",.35,1.002),("N02 系统偏差",1.5,1.045),("N03 宽范围",.2,1.008)]):
            d=PairDataset(name=name,reference_id=ins[0].id,candidate_id=ins[idx+1].id if idx<2 else ins[2].id,source_file="x.csv")
            db.add(d);db.flush()
            for i in range(180):
                latent=8+i*80/179
                x=round(latent+rng.gauss(0,.22),4)
                y=round(intercept+slope*latent+rng.gauss(0,.25),4)
                db.add(PairedReading(dataset_id=d.id,sample_code=f"CG{idx+1}-{i+1:03d}",reference_value=x,candidate_value=y))
        db.commit()
    yield

@pytest.fixture(autouse=True)
def clean_runtime_state():
    import src.middlewares.rate_limit as rl
    rl.Redis.store.clear()
    from src.models import ComparisonRun,RunVersion,RecalculationBatch,RecalculationItem,Qualification
    with Session() as db:
        for model in (RecalculationItem,RecalculationBatch,Qualification,RunVersion,ComparisonRun):
            db.query(model).delete()
        db.commit()
    yield

@pytest.fixture()
def db():
    with Session() as s:yield s

@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from src.main import app
    with TestClient(app) as c:yield c

@pytest.fixture()
def tokens(client):
    out={}
    for role in ("admin","analyst","viewer"):
        r=client.post("/auth/login",json={"email":f"{role}@ciguang.demo","password":"Ciguang2026!"})
        assert r.status_code==200,r.text
        out[role]={"Authorization":"Bearer "+r.json()["token"]}
    return out
