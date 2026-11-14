from fastapi import APIRouter,Depends
from src.config.database import get_db
from src.utils.auth import current_user,editor
from src.schemas.run import RunInput,CalibrationInput
from src.controllers.run_controller import list_runs,run_view,create_run,calibrate,list_versions,get_version
router=APIRouter(prefix="/runs")
@router.get("")
def index(db=Depends(get_db),user=Depends(current_user)):return list_runs(db)
@router.post("",status_code=201)
def create(data:RunInput,db=Depends(get_db),user=Depends(editor)):return create_run(db,data)
@router.get("/versions")
def all_versions(db=Depends(get_db),user=Depends(current_user)):return list_versions(db,0)
@router.get("/{id}")
def detail(id:int,db=Depends(get_db),user=Depends(current_user)):return run_view(db,id)
@router.get("/{id}/versions")
def versions(id:int,db=Depends(get_db),user=Depends(current_user)):return list_versions(db,id)
@router.get("/{id}/versions/{version_id}")
def version_detail(id:int,version_id:int,points:bool=False,db=Depends(get_db),user=Depends(current_user)):return get_version(db,id,version_id,include_points=points)
@router.post("/{id}/calibrate")
def inverse(id:int,data:CalibrationInput,db=Depends(get_db),user=Depends(current_user)):return calibrate(db,id,data.candidate_value,data.version_id)
