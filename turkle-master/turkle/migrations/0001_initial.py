# Generated by Django 3.2.20 on 2023-07-23 08:07

from django.conf import settings
import django.core.validators
from django.db import migrations, models
import django.db.models.deletion
import jsonfield.fields
import turkle.models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('auth', '0012_alter_user_first_name_max_length'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Batch',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('active', models.BooleanField(db_index=True, default=True)),
                ('allotted_assignment_time', models.IntegerField(default=24)),
                ('assignments_per_task', models.IntegerField(default=1, verbose_name='Assignments per Task')),
                ('completed', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('custom_permissions', models.BooleanField(default=True)),
                ('filename', models.CharField(max_length=1024)),
                ('login_required', models.BooleanField(db_index=True, default=True)),
                ('name', models.CharField(max_length=1024)),
                ('published', models.BooleanField(db_index=True, default=True)),
                ('is_majority_voting', models.BooleanField(default=False)),
                ('majority_count', models.IntegerField(blank=True, null=True)),
                ('created_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='created_batches', to=settings.AUTH_USER_MODEL, verbose_name='creator')),
            ],
            options={
                'verbose_name': 'Batch',
                'verbose_name_plural': 'Batches',
                'permissions': (('can_work_on_batch', 'Can work on Tasks for this Batch'),),
            },
            bases=(turkle.models.TaskAssignmentStatistics, models.Model),
        ),
        migrations.CreateModel(
            name='Task',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('completed', models.BooleanField(default=False)),
                ('input_csv_fields', jsonfield.fields.JSONField()),
                ('batch', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='turkle.batch', verbose_name='batch')),
            ],
            options={
                'verbose_name': 'Task',
            },
        ),
        migrations.CreateModel(
            name='ActiveUser',
            fields=[
            ],
            options={
                'ordering': ['first_name'],
                'proxy': True,
                'indexes': [],
                'constraints': [],
            },
            bases=('auth.user',),
        ),
        migrations.CreateModel(
            name='UserEvaluation',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('updated_at', models.DateTimeField(auto_now=True, db_index=True)),
                ('rating', models.FloatField(default=0)),
                ('count', models.IntegerField(default=0)),
                ('id_batch', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='turkle.batch', verbose_name='batch')),
                ('id_user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL, verbose_name='user')),
            ],
            options={
                'verbose_name': 'User Evaluation',
            },
        ),
        migrations.CreateModel(
            name='TaskAssignment',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('answers', jsonfield.fields.JSONField(blank=True)),
                ('completed', models.BooleanField(db_index=True, default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('expires_at', models.DateTimeField(null=True)),
                ('updated_at', models.DateTimeField(auto_now=True, db_index=True)),
                ('assigned_to', models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL, verbose_name='assigned_to')),
                ('task', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='turkle.task', verbose_name='task')),
            ],
            options={
                'verbose_name': 'Task Assignment',
            },
        ),
        migrations.CreateModel(
            name='Project',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('active', models.BooleanField(db_index=True, default=True)),
                ('allotted_assignment_time', models.IntegerField(default=24)),
                ('assignments_per_task', models.IntegerField(db_index=True, default=1)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('custom_permissions', models.BooleanField(default=True)),
                ('filename', models.CharField(blank=True, max_length=1024)),
                ('html_template', models.TextField()),
                ('html_template_has_submit_button', models.BooleanField(default=False)),
                ('login_required', models.BooleanField(db_index=True, default=True)),
                ('name', models.CharField(max_length=1024)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('evaluation_script', models.FileField(blank=True, null=True, upload_to='', verbose_name='evaluation script')),
                ('evaluation_threshold', models.FloatField(default=0, validators=[django.core.validators.MinValueValidator(0.0), django.core.validators.MaxValueValidator(1.0)])),
                ('calibration_limit', models.IntegerField(null=True)),
                ('calibration_rating', models.FloatField(default=0, validators=[django.core.validators.MinValueValidator(0.0), django.core.validators.MaxValueValidator(1.0)])),
                ('fieldnames', jsonfield.fields.JSONField(blank=True)),
                ('created_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='created_projects', to=settings.AUTH_USER_MODEL, verbose_name='creator')),
                ('updated_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='updated_projects', to=settings.AUTH_USER_MODEL, verbose_name='updated_by')),
            ],
            options={
                'verbose_name': 'Project',
                'ordering': ['-id'],
                'permissions': (('can_work_on', 'Can work on Tasks for this Project'),),
            },
            bases=(turkle.models.TaskAssignmentStatistics, models.Model),
        ),
        migrations.AddField(
            model_name='batch',
            name='project',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='turkle.project', verbose_name='project'),
        ),
        migrations.CreateModel(
            name='ActiveProject',
            fields=[
            ],
            options={
                'ordering': ['name'],
                'proxy': True,
                'indexes': [],
                'constraints': [],
            },
            bases=('turkle.project',),
        ),
    ]
