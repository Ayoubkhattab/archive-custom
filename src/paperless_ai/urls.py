from django.urls import path
from . import views

app_name = "paperless_ai"

urlpatterns = [
    path("suggest/", views.ai_suggest, name="suggest"),
    path("chat/", views.ai_chat, name="chat"),
    path("classify/", views.ai_classify, name="classify"),
]
