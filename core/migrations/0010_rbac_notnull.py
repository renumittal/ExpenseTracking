from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0009_rbac_backfill_and_drop_legacy'),
    ]

    operations = [
        migrations.AlterField(
            model_name='rolepermission',
            name='role_fk',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE, related_name='role_permissions',
                to='core.accessrole', db_column='role_id',
            ),
        ),
        migrations.AlterField(
            model_name='rolepermission',
            name='resource_permission',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE, related_name='role_permissions',
                to='core.resourcepermission',
            ),
        ),
        migrations.AddConstraint(
            model_name='rolepermission',
            constraint=models.UniqueConstraint(
                fields=('role_fk', 'resource_permission'), name='unique_role_fk_resource_permission',
            ),
        ),
    ]
