# Generated by Django 3.1.12 on 2021-11-12 14:38

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mathesar', '0023_column'),
    ]

    operations = [
        migrations.RenameField(
            model_name='column',
            old_name='index',
            new_name='attnum',
        ),
        migrations.AddField(
            model_name='column',
            name='display_options',
            field=models.JSONField(null=True),
        ),
    ]
