from celery import shared_task
from django.apps import apps
from django.db import transaction, models
from django_elasticsearch_dsl.registries import registry
from django_elasticsearch_dsl.signals import BaseSignalProcessor


@shared_task
def handle_save(pk, app_label, model_name):
    sender = apps.get_model(app_label, model_name)
    instance = sender.objects.get(pk=pk)
    registry.update(instance)
    registry.update_related(instance)


@shared_task
def handle_pre_delete(pk, app_label, model_name):
    sender = apps.get_model(app_label, model_name)
    fake_instance = sender(pk=pk)
    registry.delete_related(fake_instance)


@shared_task
def handle_delete(pk, app_label, model_name):
    sender = apps.get_model(app_label, model_name)
    fake_instance = sender(pk=pk)
    registry.delete(fake_instance, raise_on_error=False)


class CelerySignalProcessor(BaseSignalProcessor):
    """Celery signal processor.
    Allows automatic updates on the index as delayed background tasks using
    Celery.
    NB: We cannot process deletes as background tasks.
    By the time the Celery worker would pick up the delete job, the
    model instance would already deleted. We can get around this by
    setting Celery to use `pickle` and sending the object to the worker,
    but using `pickle` opens the application up to security concerns.
    """

    def setup(self):
        # Listen to all model saves.
        models.signals.post_save.connect(self.handle_save)
        models.signals.post_delete.connect(self.handle_delete)

        # Use to manage related objects update
        models.signals.m2m_changed.connect(self.handle_m2m_changed)
        models.signals.pre_delete.connect(self.handle_pre_delete)

    def teardown(self):
        # Listen to all model saves.
        models.signals.post_save.disconnect(self.handle_save)
        models.signals.post_delete.disconnect(self.handle_delete)
        models.signals.m2m_changed.disconnect(self.handle_m2m_changed)
        models.signals.pre_delete.disconnect(self.handle_pre_delete)

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
