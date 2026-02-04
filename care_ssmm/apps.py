from django.apps import AppConfig

PLUGIN_NAME = "care_ssmm"


class CareSSMMConfig(AppConfig):
    name = PLUGIN_NAME

    def ready(self):
        import care_ssmm.signals # noqa 
        import care_ssmm.authorizers # noqa