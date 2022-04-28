from django import forms
from django.forms import inlineformset_factory
from tom_targets.models import TargetList
from .models import TargetListExtra


TargetListExtraFormset = inlineformset_factory(TargetList, TargetListExtra, fields=('key', 'value'),
                                               widgets={'value': forms.TextInput()})
