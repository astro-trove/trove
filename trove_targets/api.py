from typing import List
from astropy.time import Time, TimezoneInfo
from ninja import Router, Schema
from ninja.orm import create_schema
from tom_nonlocalizedevents.models import EventCandidate
from tom_targets.utils import cone_search_filter
from trove_targets.models import Target
from tom_targets.models import TargetName
from trove_targets.hooks import target_hook_options
from trove_targets.tasks import async_target_post_save
from tom_dataproducts.models import PhotometryReducedDatum

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
    
    # now save the target if it doesn't exist already
    target = cone_search_filter(
        Target.objects.all(),
        payload_dict["ra"],
        payload_dict["dec"],
        2/3600, # radius, 2 arcsec converted to degrees
    ).order_by("separation").first()
    if target is None:
        target = Target(**payload_dict)
        target.full_clean(validate_unique=False)
        with target_hook_options(skip_vetting=True):
            target.save()
    
    # add the alias from this name, if needed
    name = payload_dict["name"]
    if name != target.name and not TargetName.objects.filter(name=name).exists():
        target.aliases.create(name=name)
    
    # then save the photometry, if provided
    if phot is not None:
        uploaded_phot = []
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

            value = {
                "source_name":source,
                "telescope":source,
                "bandpass":d.get("filter"),
            }
            if "magnitude" in d:
                value["brightness"] = d.pop("magnitude")
            elif "limit" in d:
                value["limit"] = d.pop("limit")

            if "error" in d:
                value["brightness_error"] = d.pop("error")
            

            uploaded_phot.append(
                PhotometryReducedDatum(
                    target=target,
                    timestamp=time,
                    **value
                )
            )
            

        PhotometryReducedDatum.objects.bulk_create(
            uploaded_phot, ignore_conflicts=True
        )
                 
    # run the target post save hook
    async_target_post_save.enqueue(target.id)

    return target

@router.post("/upload")
def upload_target(request, payload: List[UploadTargetSchema]):
    """
    API Endpoint to upload new targets and data. See the UploadTargetSchema
    and PhotometrySchema for the format of the payload argument.

    *Example:*

    DON'T FORGET to change <api_token> to your API token, found on your TROVE user profile page!
    
    To upload a target without photometry

    ```
    curl -X 'POST'   'https://datatrove.as.arizona.edu/api/target/upload'   -H 'accept: */*'   -H 'Content-Type: application/json' -H 'Authorization: Bearer <api_token>'  -d '[
      {
        "name": "test",
        "ra": 0,
        "dec": 0
      }
    ]'
    ```
    
    To upload a target with photometry:

    ```
    curl -X 'POST'   'https://datatrove.as.arizona.edu/api/target/upload'   -H 'accept: */*'   -H 'Content-Type: application/json'  -H 'Authorization: Bearer <api_token>'  -d '[
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
    curl -X 'POST'   'https://datatrove.as.arizona.edu/api/target/upload'   -H 'accept: */*'   -H 'Content-Type: application/json'  -H 'Authorization: Bearer <api_token>'  -d '[
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
