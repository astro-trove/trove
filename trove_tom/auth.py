from ninja import NinjaAPI
from ninja.security import HttpBasicAuth
from base64 import b64decode
from django.contrib.auth import authenticate
from django.http import HttpRequest
from typing import Optional

class BasicAuth(HttpBasicAuth):
    def authenticate(self, request: HttpRequest, username:str, password:str) -> Optional[object]:
        return authenticate(request=request, username=username, password=password)
