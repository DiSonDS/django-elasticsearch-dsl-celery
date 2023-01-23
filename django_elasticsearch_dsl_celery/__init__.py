from celery import shared_task
from django.apps import apps
from django.db import transaction, models
from django_elasticsearch_dsl.registries import registry
from django_elasticsearch_dsl.signals import BaseSignalProcessor

INDEXED_MODELS = registry.get_models()


@shared_task(ignore_result=True)
def handle_save(pk, app_label, model_name):
    sender = apps.get_model(app_label, model_name)
    instance = sender.objects.get(pk=pk)
    registry.update(instance)
    registry.update_related(instance)


@shared_task(ignore_result=True)
def handle_pre_delete(pk, app_label, model_name):
    sender = apps.get_model(app_label, model_name)
    fake_instance = sender(pk=pk)
    registry.delete_related(fake_instance)


@shared_task(ignore_result=True)
def handle_delete(pk, app_label, model_name):
    sender = apps.get_model(app_label, model_name)
    fake_instance = sender(pk=pk)
    registry.delete(fake_instance, raise_on_error=False)


class CelerySignalProcessor(BaseSignalProcessor):
    """Celery signal processor.

    Allows automatic updates on the index as delayed background tasks using
    Celery.
    """

    def setup(self):
        """Set up.

        A hook for setting up anything necessary for
        ``handle_save/handle_delete`` to be executed.
        """
        for model in INDEXED_MODELS:
            models.signals.post_save.connect(self.handle_save, sender=model)
            models.signals.post_delete.connect(self.handle_delete, sender=model)
            models.signals.m2m_changed.connect(self.handle_m2m_changed, sender=model)
            models.signals.pre_delete.connect(self.handle_pre_delete, sender=model)

    def teardown(self):
        """Tear-down.

        A hook for tearing down anything necessary for
        ``handle_save/handle_delete`` to no longer be executed.
        """
        for model in INDEXED_MODELS:
            models.signals.post_save.disconnect(self.handle_save, sender=model)
            models.signals.post_delete.disconnect(self.handle_delete, sender=model)
            models.signals.m2m_changed.disconnect(self.handle_m2m_changed, sender=model)
            models.signals.pre_delete.disconnect(self.handle_pre_delete, sender=model)

    def handle_save(self, sender, instance, **kwargs):
        """Handle save.

        Given an individual model instance, update the object in the index.
        Update the related objects either.
        """
        app_label = instance._meta.app_label
        model_name = instance._meta.model_name
        transaction.on_commit(
            lambda: handle_save.delay(instance.pk, app_label, model_name)
        )

    def handle_pre_delete(self, sender, instance, **kwargs):
        """Handle removing of instance object from related models instance.

        We need to do this before the real delete otherwise the relation
        doesn't exists anymore and we can't get the related models instance.
        """
        app_label = instance._meta.app_label
        model_name = instance._meta.model_name
        handle_pre_delete.delay(instance.pk, app_label, model_name)

    def handle_delete(self, sender, instance, **kwargs):
        """Handle delete.

        Given an individual model instance, delete the object from index.
        """
        app_label = instance._meta.app_label
        model_name = instance._meta.model_name
        handle_delete.delay(instance.pk, app_label, model_name)
