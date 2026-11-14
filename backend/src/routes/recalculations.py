from fastapi import APIRouter,Depends
from src.config.database import get_db
from src.utils.auth import current_user,editor
from src.schemas.run import RecalculationInput
from src.controllers.recalculation_controller import list_batches,create_recalculation,revoke_batch,activate_batch,get_batch
router=APIRouter(prefix="/recalculations")
@router.get("")
def index(db=Depends(get_db),user=Depends(current_user)):return list_batches(db)
@router.get("/{id}")
def detail(id:int,db=Depends(get_db),user=Depends(current_user)):return get_batch(db,id)
@router.post("",status_code=201)
def create(data:RecalculationInput,db=Depends(get_db),user=Depends(editor)):
    batch,items,reused=create_recalculation(db,data,user)
    payload=get_batch(db,batch.id);payload["reused"]=reused
    return payload
@router.post("/{id}/revoke")
def revoke(id:int,db=Depends(get_db),user=Depends(editor)):
    revoke_batch(db,id,user);return get_batch(db,id)
@router.post("/{id}/activate")
def activate(id:int,db=Depends(get_db),user=Depends(editor)):
    activate_batch(db,id,user);return get_batch(db,id)
