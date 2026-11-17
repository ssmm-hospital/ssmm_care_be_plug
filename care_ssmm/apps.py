from django.apps import AppConfig

PLUGIN_NAME = "care_ssmm"


class CareSSMMConfig(AppConfig):
    name = PLUGIN_NAME

    def ready(self):
        import care_ssmm.authorizers  # noqa: F401
        import care_ssmm.signals  # noqa: F401
        import care_ssmm.tasks  # noqa: F401
