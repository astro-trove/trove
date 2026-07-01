from ninja import NinjaAPI
from .auth import BasicAuth

api = NinjaAPI()
basic_auth = BasicAuth()

api.add_router("/score/", "scoring.api.router", auth=basic_auth)
api.add_router("/target/", "trove_targets.api.router", auth=basic_auth)
