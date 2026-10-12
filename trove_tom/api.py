from ninja import NinjaAPI
from .auth import BasicAuth, TokenAuth

api = NinjaAPI()
auth = [TokenAuth(), BasicAuth()]

api.add_router("/score/", "scoring.api.router", auth=auth)
api.add_router("/target/", "trove_targets.api.router", auth=auth)
