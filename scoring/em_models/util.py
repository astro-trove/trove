import glob
import os
from .schema import TransientModelSchema

MODEL_FILES = glob.glob(
    os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "*json"
    )
)

MODELS = [TransientModelSchema.from_json(j) for j in MODEL_FILES]

MODELS_BY_NAME = {t.name:t for t in MODELS}
