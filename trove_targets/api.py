from typing import List
from astropy.time import Time, TimezoneInfo
from ninja import Router, Schema
from ninja.orm import create_schema
from tom_nonlocalizedevents.models import EventCandidate
from tom_targets.utils import cone_search_filter
from trove_targets.models import Target
from custom_code.hooks import target_post_save
from candidate_vetting.public_catalogs.util import create_phot

router = Router()

class PhotometrySchema(Schema):
    jd: float
    telescope: str
    filter: str
    magnitude: float = None
    error: float = None
    limit: float = None
    
class UploadTargetSchema(Schema):
    name: str
    ra: float
    dec: float
    permissions: str = "PUBLIC"
    type: str = "SIDEREAL"
    epoch: int = 2000
    photometry: List[PhotometrySchema] = None

def _save_target(payload:UploadTargetSchema):
    
    payload_dict = payload.dict()

    # delete the photometry and clean the photometry dictionary
    # to remove unset values
    del payload_dict["photometry"]
    phot = payload.photometry
    
    # now save the target
    target, _ = Target.objects.get_or_create(**payload_dict)
    target.full_clean()
    target.save()

    # then save the photometry
    if phot is not None:
        for d in phot:
            d = d.dict(exclude_unset=True)
            jd = d.pop("jd")
            time = Time(
                jd,
                format="jd",
                scale="utc"
            ).to_datetime(
                timezone=TimezoneInfo()
            )
            source = d.pop("telescope")
            
            create_phot(
                target = target,
                time = time,
                fluxdict = d,
                source = source
            )
    
    # run the target post save hook
    target_post_save(target, created=True)

    return target

@router.post("/upload")
def upload_target(request, payload: List[UploadTargetSchema]):
    """
    API Endpoint to upload new targets and data. See the UploadTargetSchema
    and PhotometrySchema for the format of the payload argument.

    *Example:*

    DON'T FORGET to change <username> and <password> to your username and password! 
    
    To upload a target without photometry

    ```
    curl -X 'POST'   'http://localhost:8000/api/target/upload'   -H 'accept: */*'   -H 'Content-Type: application/json' -u <username>:<password>  -d '[
      {
        "name": "test",
        "ra": 0,
        "dec": 0
      }
    ]'
    ```
    
    To upload a target with photometry:

    ```
    curl -X 'POST'   'http://localhost:8000/api/target/upload'   -H 'accept: */*'   -H 'Content-Type: application/json'  -u <username>:<password>  -d '[
      {
        "name": "test",
        "ra": 0,
        "dec": 0,
        "photometry": [
          {
            "jd": 2461134.3933751667,
            "telescope": "TEST",
            "filter": "r",
            "magnitude": 20,
            "error": 0.1
          },
          {
            "jd": 2461133.3933751667,
            "telescope": "TEST",
            "filter": "r",
            "limit": 23.1
          }
        ]
      }
    ]'
    ```
    
    To upload a new photometry point to an existing target 

    ```
    curl -X 'POST'   'http://localhost:8000/api/target/upload'   -H 'accept: */*'   -H 'Content-Type: application/json'  -u <username>:<password>  -d '[
      {
        "name": "test",
        "ra": 0,
        "dec": 0,
        "photometry": [
          {
            "jd": 2461135.3933751667,
            "telescope": "TEST",
            "filter": "g",
            "magnitude": 19.8,
            "error": 0.1
          }
        ]
      }
    ]'
    ```
    
    """
    try:
        targets = [_save_target(p) for p in payload]            
        return {"status": "success", "id": [t.id for t in targets]}

    except Exception as e:
        return {"status": "error", "message": str(e)}
