# Django
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("hostile", "0004_alter_general_options")]

    operations = [
        migrations.AddField(
            model_name="hostilestructure",
            name="moonmining_moon_id",
            field=models.BigIntegerField(
                blank=True,
                help_text="Manually correlated aa-moonmining EveMoon ID",
                null=True,
            ),
        ),
    ]
