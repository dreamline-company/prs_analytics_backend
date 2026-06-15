from apps.celery_app import celery_app


@celery_app.task(name="wells.sync_wells")
def sync_wells():
    pass
