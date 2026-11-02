# Django
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("hostile", "0005_hostilestructure_moonmining_moon_id")]

    operations = [
        migrations.AddField(
            model_name="structuretimer",
            name="structuretimers_adopted",
            field=models.BooleanField(
                default=False,
                help_text="Linked to a pre-existing aa-structuretimers timer posted by someone else; never overwritten from Hostile Intel",
            ),
        ),
        migrations.AddField(
            model_name="hostilestructure",
            name="last_notification_at",
            field=models.DateTimeField(
                blank=True,
                help_text="Timestamp of the newest EVE notification that set this structure's state",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="structuretimer",
            name="source",
            field=models.CharField(
                choices=[
                    ("MANUAL", "Manual / Intel"),
                    ("NOTIFICATION", "EVE Notification"),
                    ("EXTERNAL", "External Timerboard"),
                ],
                default="MANUAL",
                help_text="Where this timer originated",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="structuretimer",
            name="source_notification_id",
            field=models.BigIntegerField(
                blank=True,
                db_index=True,
                help_text="EVE notification ID this timer was created from",
                null=True,
            ),
        ),
    ]
