from ninja import NinjaAPI

api = NinjaAPI()

api.add_router("/score/", "candidate_vetting.api.router")
api.add_router("/target/", "trove_targets.api.router")
