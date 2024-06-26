from collections import defaultdict
from datetime import datetime, timedelta
from functools import wraps
import logging
import urllib

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth import login
from django.core.exceptions import ObjectDoesNotExist
from django.db import connection, transaction
from django.db.utils import OperationalError
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.datastructures import MultiValueDictKeyError
from .models import Task, TaskAssignment, Batch, Project, UserEvaluation, deactivate_user
from .forms import SignUpForm

User = get_user_model()

logger = logging.getLogger(__name__)


def handle_db_lock(func):
    """Decorator that catches database lock errors from sqlite"""
    @wraps(func)
    def wrapper(request, *args, **kwargs):
        try:
            return func(request, *args, **kwargs)
        except OperationalError as ex:
            # sqlite3 cannot handle concurrent transactions.
            # This should be very rare with just a few users.
            # If it happens often, switch to mysql or postgres.
            if str(ex) == 'database is locked':
                messages.error(request, u'The database is busy. Please try again.')
                return redirect(index)
            raise ex
    return wrapper


def index(request):
    """
    Security behavior:
    - Anyone can access the page, but the page only shows the user
      information they have access to.
    """
    abandoned_assignments = []
    if request.user.is_authenticated:
        for ha in TaskAssignment.objects.filter(assigned_to=request.user)\
                                        .filter(completed=False)\
                                        .filter(task__batch__active=True)\
                                        .filter(task__batch__project__active=True):
            abandoned_assignments.append({
                'task': ha.task,
                'task_assignment_id': ha.id
            })

    batch_list = Batch.access_permitted_for(request.user)
    batch_query = Batch.objects.filter(id__in=[b.id for b in batch_list])

    available_task_counts = Batch.available_task_counts_for(batch_query, request.user)

    batch_rows = []
    for batch in batch_query.values('created_at', 'id', 'name', 'project__name'):
        total_tasks_available = available_task_counts[batch['id']]
        print(batch['created_at'])

        if total_tasks_available > 0:
            batch_rows.append({
                'project_name': batch['project__name'],
                'batch_name': batch['name'],
                'batch_published': batch['created_at'],
                'assignments_available': total_tasks_available,
                'preview_next_task_url': reverse('preview_next_task',
                                                 kwargs={'batch_id': batch['id']}),
                'accept_next_task_url': reverse('accept_next_task',
                                                kwargs={'batch_id': batch['id']})
            })

    batch_rows = sorted(batch_rows, key=lambda x: x['project_name'])

    return render(request, 'turkle/index.html', {
        'abandoned_assignments': abandoned_assignments,
        'batch_rows': batch_rows
    })


@handle_db_lock
def accept_task(request, batch_id, task_id):
    """
    Accept task from preview

    Security behavior:
    - If the user does not have permission to access the Batch+Task, they
      are redirected to the index page with an error message.
    """
    try:
        batch = Batch.objects.get(id=batch_id)
    except ObjectDoesNotExist:
        messages.error(request, u'Cannot find Task Batch with ID {}'.format(batch_id))
        return redirect(index)
    try:
        task = Task.objects.get(id=task_id)
    except ObjectDoesNotExist:
        messages.error(request, u'Cannot find Task with ID {}'.format(task_id))
        return redirect(index)

    try:
        with transaction.atomic():
            # Lock access to the specified Task
            # len() forces execution of query to create lock
            len(Task.objects.filter(id=task_id).select_for_update())

            # Will throw ObjectDoesNotExist exception if Task no longer available
            batch.available_tasks_for(request.user).get(id=task_id)

            ha = TaskAssignment()
            if request.user.is_authenticated:
                ha.assigned_to = request.user
            else:
                ha.assigned_to = None
            ha.task = task
            ha.save()
            if request.user.is_authenticated:
                logger.info('User(%i) accepted Task(%i)', request.user.id, task.id)
            else:
                logger.info('Anonymous user accepted Task(%i)', task.id)
    except ObjectDoesNotExist:
        messages.error(request, u'The Task with ID {} is no longer available'.format(task_id))
        return redirect(index)

    return redirect(task_assignment, task.id, ha.id)


@handle_db_lock
def accept_next_task(request, batch_id):
    """
    Accept task from index or auto accept next task

    Security behavior:
    - If the user does not have permission to access the Batch+Task, they
      are redirected to the index page with an error message.
    """
    try:
        with transaction.atomic():
            batch = Batch.objects.get(id=batch_id)

            # Lock access to all Tasks available to current user in the batch
            # Force evaluation of the query set with len()
            # But postgres does not support select_for_update() with outer join
            if connection.vendor != "postgresql":
                len(batch.available_task_ids_for(request.user).select_for_update())
            else:
                # Locks a few more tasks for multiple annotation batches than above
                len(batch.unfinished_tasks().select_for_update())

            task_id = _skip_aware_next_available_task_id(request, batch)

            if task_id:
                ha = TaskAssignment()
                if request.user.is_authenticated:
                    ha.assigned_to = request.user
                else:
                    ha.assigned_to = None
                ha.task_id = task_id
                ha.save()
                if request.user.is_authenticated:
                    logger.info('User(%i) accepted Task(%i)', request.user.id, task_id)
                else:
                    logger.info('Anonymous user accepted Task(%i)', task_id)
    except ObjectDoesNotExist:
        messages.error(request, u'Cannot find Task Batch with ID {}'.format(batch_id))
        return redirect(index)

    if task_id:
        return redirect(task_assignment, task_id, ha.id)
    else:
        messages.error(request, u'No more Tasks available for Batch {}'.format(batch.name))
        return redirect(index)


def help_page(request):
    return render(request, 'turkle/help.html')

def base_page(request):
    return render(request, 'turkle/base.html')

def task_assignment(request, task_id, task_assignment_id):
    """
    Task view and submission (task content in iframe below)

    Security behavior:
    - If the user does not have permission to access the Task Assignment, they
      are redirected to the index page with an error message.
    """
    try:
        task = Task.objects.get(id=task_id)
    except ObjectDoesNotExist:
        messages.error(request, 'Cannot find Task with ID {}'.format(task_id))
        return redirect(index)
    try:
        task_assignment = TaskAssignment.objects.get(id=task_assignment_id)
    except ObjectDoesNotExist:
        messages.error(request,
                       'Cannot find Task Assignment with ID {}'.format(task_assignment_id))
        return redirect(index)

    if request.user.is_authenticated:
        if request.user != task_assignment.assigned_to:
            messages.error(
                request,
                'You do not have permission to work on the Task Assignment with ID {}'.
                format(task_assignment.id))
            return redirect(index)
    else:
        if task_assignment.assigned_to is not None:
            messages.error(
                request,
                'You do not have permission to work on the Task Assignment with ID {}'.
                format(task_assignment.id))
            return redirect(index)

    auto_accept_status = request.session.get('auto_accept_status', False)


    if request.method == 'GET':
        http_get_params = "?assignmentId={}&hitId={}&workerId={}&urlSubmitTo={}".format(
            task_assignment.id,
            task.id,
            request.user.id,
            urllib.parse.quote(
                reverse('task_assignment', kwargs={
                    'task_id': task.id, 'task_assignment_id': task_assignment.id}),
                safe=''))
        return render(
            request,
            'turkle/task_assignment.html',
            {
                'auto_accept_status': auto_accept_status,
                'http_get_params': http_get_params,
                'task': task,
                'task_assignment': task_assignment,
            },
        )
    else:
        # check the  new rating for user for a calibration task
        if 'calibration_answer' in task.input_csv_fields:
            if task.input_csv_fields['calibration_answer'] != '0':
                task.calibration_rate_for_the_task(dict(request.POST.items()), request.user)
                user_evaluation = UserEvaluation.objects.get(id_user=request.user, id_batch=task.batch)
                deactivation = UserEvaluation.deactivate_user_with_low_rating(user_evaluation, request.user)
                if deactivation == 1:
                    return redirect(index)

        task_assignment.answers = dict(request.POST.items())
        task_assignment.completed = True
        task_assignment.save()

        majority_voting = task.batch.is_majority_voting
        if majority_voting == 1:
            done_taskassignments = TaskAssignment.objects.filter(task_id=task)
            assignment_count = task.batch.assignments_per_task
            minimum = task.batch.majority_count
            if len(done_taskassignments) >= minimum:
                TaskAssignment.check_majority_voting(done_taskassignments, task_id)

        if request.user.is_authenticated:
            logger.info('User(%i) submitted Task(%i)', request.user.id, task.id)
        else:
            logger.info('Anonymous user submitted Task(%i)', task.id)

        if request.session.get('auto_accept_status'):
            return redirect(accept_next_task, task.batch.id)
        else:
            return redirect(index)


def task_assignment_iframe(request, task_id, task_assignment_id):
    """
    Task form served in an iframe

    Security behavior:
    - If the user does not have permission to access the Task Assignment, they
      are redirected to the index page with an error messge.
    """
    try:
        task = Task.objects.get(id=task_id)
    except ObjectDoesNotExist:
        messages.error(request, 'Cannot find Task with ID {}'.format(task_id))
        return redirect(index)
    try:
        task_assignment = TaskAssignment.objects.get(id=task_assignment_id)
    except ObjectDoesNotExist:
        messages.error(request,
                       'Cannot find Task Assignment with ID {}'.format(task_assignment_id))
        return redirect(index)

    if request.user.is_authenticated:
        if request.user != task_assignment.assigned_to:
            messages.error(
                request,
                'You do not have permission to work on the Task Assignment with ID {}'.
                format(task_assignment.id))
            return redirect(index)

    return render(
        request,
        'turkle/task_assignment_iframe.html',
        {
            'task': task,
            'task_assignment': task_assignment,
        },
    )


def preview(request, task_id):
    """
    Security behavior:
    - If the user does not have permission to access the Task, they
      are redirected to the index page with an error message.
    """
    try:
        task = Task.objects.get(id=task_id)
    except ObjectDoesNotExist:
        messages.error(request, 'Cannot find Task with ID {}'.format(task_id))
        return redirect(index)

    if not task.batch.project.available_for(request.user):
        messages.error(request, 'You do not have permission to view this Task')
        return redirect(index)

    http_get_params = "?assignmentId=ASSIGNMENT_ID_NOT_AVAILABLE&hitId={}".format(
        task.id)
    return render(request, 'turkle/preview.html', {
        'http_get_params': http_get_params,
        'task': task
    })


def preview_iframe(request, task_id):
    """
    Security behavior:
    - If the user does not have permission to access the Task, they
      are redirected to the index page with an error message.
    """
    try:
        task = Task.objects.get(id=task_id)
    except ObjectDoesNotExist:
        messages.error(request, 'Cannot find Task with ID {}'.format(task_id))
        return redirect(index)

    if not task.batch.project.available_for(request.user):
        messages.error(request, 'You do not have permission to view this Task')
        return redirect(index)

    return render(request, 'turkle/preview_iframe.html', {'task': task})


def preview_next_task(request, batch_id):
    """
    Security behavior:
    - If the user does not have permission to access the Batch, they
      are redirected to the index page with an error message.
    """
    try:
        batch = Batch.objects.get(id=batch_id)
    except ObjectDoesNotExist:
        messages.error(request, 'Cannot find Task Batch with ID {}'.format(batch_id))
        return redirect(index)

    task_id = _skip_aware_next_available_task_id(request, batch)

    if task_id:
        return redirect(preview, task_id)
    else:
        messages.error(request,
                       'No more Tasks are available for Batch "{}"'.format(batch.name))
        return redirect(index)


def return_task_assignment(request, task_id, task_assignment_id):
    """
    Security behavior:
    - If the user does not have permission to return the Assignment, they
      are redirected to the index page with an error message.
    """
    redirect_due_to_error = _delete_task_assignment(request, task_id, task_assignment_id)
    if redirect_due_to_error:
        return redirect_due_to_error
    if request.user.is_authenticated:
        logger.info('User(%i) returned Task(%i)', request.user.id, int(task_id))
    else:
        logger.info('Anonymous user returned Task(%i)', int(task_id))
    return redirect(index)


def skip_and_accept_next_task(request, batch_id, task_id, task_assignment_id):
    """
    Security behavior:
    - If the user does not have permission to return the Assignment, they
      are redirected to the index page with an error message.
    """
    redirect_due_to_error = _delete_task_assignment(request, task_id, task_assignment_id)
    if redirect_due_to_error:
        return redirect_due_to_error

    _add_task_id_to_skip_session(request.session, batch_id, task_id)
    if request.user.is_authenticated:
        logger.info('User(%i) skipped Task(%i)', request.user.id, int(task_id))
    else:
        logger.info('Anonymous user skipped Task(%i)', int(task_id))
    return redirect(accept_next_task, batch_id)


def skip_task(request, batch_id, task_id):
    """
    Skip to next task when previewing a task

    Security behavior:
    - This view updates a session variable that controls the order
      that Tasks are presented to a user.  Users cannot modify other
      users session variables.
    """
    _add_task_id_to_skip_session(request.session, batch_id, task_id)
    return redirect(preview_next_task, batch_id)


def stats_for_self(request):
    if not request.user.is_authenticated:
        messages.error(
            request,
            u'You must be logged in to view the user statistics page')
        return redirect(index)

    return stats_for_user(request, request.user.id)


def parse_date_with_timezone(value):
    """wrapper for Django's parse_date() that uses timezone when appropriate"""
    date = parse_date(value)
    dt = datetime(date.year, date.month, date.day)
    if settings.USE_TZ:
        dt = timezone.make_aware(dt)
    return dt


def stats_for_user(request, user_id):
    def format_seconds(s):
        """Converts seconds to string"""
        return '%dh %dm' % (s//3600, (s//60) % 60)

    try:
        user = User.objects.get(id=user_id)
    except ObjectDoesNotExist:
        messages.error(request, u'Cannot find User with ID {}'.format(user_id))
        return redirect(index)

    if request.user.id != user_id and not request.user.is_staff:
        messages.error(request, u"You cannot view another User's statistics unless you are Staff")
        return redirect(index)

    try:
        start_date = parse_date_with_timezone(request.GET['start_date'])
    except MultiValueDictKeyError:
        start_date = None
    try:
        end_date = parse_date_with_timezone(request.GET['end_date'])
    except MultiValueDictKeyError:
        end_date = None

    tas = TaskAssignment.objects.filter(completed=True).filter(assigned_to=user)
    if start_date:
        tas = tas.filter(updated_at__gte=start_date)
    if end_date:
        # adds a day to include assignments completed on the selected end date
        tas = tas.filter(updated_at__lte=end_date + timedelta(days=1))

    projects = Project.objects.filter(batch__task__taskassignment__assigned_to=user).\
        distinct()
    batches = Batch.objects.filter(task__taskassignment__assigned_to=user).distinct()

    elapsed_seconds_overall = 0
    project_stats = []
    for project in projects:
        project_batches = [b for b in batches if b.project_id == project.id]

        batch_stats = []
        elapsed_seconds_project = 0
        total_completed_project = 0
        blank_answers_project = 0
        for batch in project_batches:
            batch_tas = tas.filter(task__batch=batch)
            total_completed_batch = batch_tas.count()
            total_completed_project += total_completed_batch
            elapsed_seconds_batch = sum([ta.work_time_in_seconds() for ta in batch_tas])
            elapsed_seconds_project += elapsed_seconds_batch
            elapsed_seconds_overall += elapsed_seconds_batch
            blank_answers_batch = batch_tas.filter(answers="").count()
            blank_answers_project += blank_answers_batch
            if total_completed_batch > 0:
                batch_stats.append({
                    'batch_name': batch.name,
                    'elapsed_time_batch': format_seconds(elapsed_seconds_batch),
                    'total_completed_batch': total_completed_batch,
                    'blank_answers_batch': blank_answers_batch,
                })
        if total_completed_project > 0:
            project_stats.append({
                'project_name': project.name,
                'batch_stats': batch_stats,
                'elapsed_time_project': format_seconds(elapsed_seconds_project),
                'total_completed_project': total_completed_project,
                'blank_answers_project': blank_answers_project
            })

    if start_date:
        start_date = start_date.strftime('%Y-%m-%d')
    if end_date:
        end_date = end_date.strftime('%Y-%m-%d')

    name = user.get_full_name()
    if not name:
        name = user.get_username()

    return render(
        request,
        'turkle/stats.html',
        {
            'project_stats': project_stats,
            'end_date': end_date,
            'start_date': start_date,
            'total_completed': tas.count(),
            'total_elapsed_time': format_seconds(elapsed_seconds_overall),
            'full_name': name,
            'user_id': user.id
        }
    )


def update_auto_accept(request):
    """
    Security behavior:
    - This view updates a session variable that controls whether or
      not Task Assignments are auto-accepted.  Users cannot modify other
      users session variables.
    """
    accept_status = (request.POST['auto_accept'] == 'true')
    request.session['auto_accept_status'] = accept_status
    return JsonResponse({})


def user_activity_json(request, user_id):
    if request.user.id != user_id and not request.user.is_staff:
        return JsonResponse({})

    try:
        user = User.objects.get(id=user_id)
    except ObjectDoesNotExist:
        return JsonResponse({})

    # Create dictionary mapping timestamp (in seconds) to number of TaskAssignments
    # completed at that timestamp
    completed_at = TaskAssignment.objects.\
        filter(completed=True).\
        filter(assigned_to=user).\
        values_list('updated_at', flat=True)
    timestamp_counts = defaultdict(int)
    for ca in completed_at:
        timestamp_counts[int(ca.timestamp())] += 1

    return JsonResponse(timestamp_counts)


def _add_task_id_to_skip_session(session, batch_id, task_id):
    """Add Task ID to session variable tracking Tasks the user has skipped
    """
    # The Django session store converts dictionary keys from ints to strings
    batch_id = str(batch_id)
    task_id = str(task_id)

    if 'skipped_tasks_in_batch' not in session:
        session['skipped_tasks_in_batch'] = {}
    if batch_id not in session['skipped_tasks_in_batch']:
        session['skipped_tasks_in_batch'][batch_id] = []
        session.modified = True
    if task_id not in session['skipped_tasks_in_batch'][batch_id]:
        session['skipped_tasks_in_batch'][batch_id].append(task_id)
        session.modified = True


@handle_db_lock
def _delete_task_assignment(request, task_id, task_assignment_id):
    """Delete a TaskAssignment, if possible

    Returns:
        - None if the TaskAssignment can be deleted, *OR*
        - An HTTPResponse object created by redirect() if there was an error

    Usage:
        redirect_due_to_error = _delete_task_assignment(request, task_id, task_assignment_id)
        if redirect_due_to_error:
            return redirect_due_to_error
    """
    try:
        task = Task.objects.get(id=task_id)
    except ObjectDoesNotExist:
        messages.error(request, 'Cannot find Task with ID {}'.format(task_id))
        return redirect(index)
    try:
        task_assignment = TaskAssignment.objects.get(id=task_assignment_id)
    except ObjectDoesNotExist:
        messages.error(request,
                       'Cannot find Task Assignment with ID {}'.format(task_assignment_id))
        return redirect(index)

    if task_assignment.completed:
        messages.error(request, u"The Task can't be returned because it has been completed")
        return redirect(index)
    if request.user.is_authenticated:
        if task_assignment.assigned_to != request.user:
            messages.error(request, 'The Task you are trying to return belongs to another user')
            return redirect(index)
    else:
        if task_assignment.assigned_to is not None:
            messages.error(request, 'The Task you are trying to return belongs to another user')
            return redirect(index)
        if task.batch.project.login_required:
            messages.error(request, 'You do not have permission to access this Task')
            return redirect(index)

    task_assignment.delete()


def _skip_aware_next_available_task_id(request, batch):
    """Get next available Task for user, taking into account previously skipped Tasks

    This function will first look for an available Task that the user
    has not previously skipped.  If the only available Tasks are Tasks
    that the user has skipped, this function will return the first
    such Task.

    Returns:
        Task ID (int), or None if no more Tasks are available
    """
    def _get_skipped_task_ids_for_batch(session, batch_id):
        batch_id = str(batch_id)
        if 'skipped_tasks_in_batch' in session and \
           batch_id in session['skipped_tasks_in_batch']:
            return session['skipped_tasks_in_batch'][batch_id]
        else:
            return None
    project = batch.project
    threshold = project.evaluation_threshold
    total_tasks_done_bt_user = TaskAssignment.objects.filter(task__batch_id=batch, assigned_to_id=request.user)
    try:
        user_evaluation = UserEvaluation.objects.get(id_batch=batch.id, id_user_id=request.user)
        # user has done evaluation for this batch before
        user_calibration_count = user_evaluation.count
    except:
        # user has not done evaluation for this batch before
        user_calibration_count = 0

    if len(total_tasks_done_bt_user) != 0:
        user_threshold = user_calibration_count / len(total_tasks_done_bt_user)
    else:
        user_threshold = 1

    skipped_ids = _get_skipped_task_ids_for_batch(request.session, batch.id)

    if threshold != 0:
        available_tasks = batch.available_tasks_for(request.user)
        task_ids = None
        # user should do a normal task
        if user_threshold >= threshold:
            task_ids = batch.none_calibration_task_ids(available_tasks)
        # user should do a calibration task
        elif user_threshold < threshold:
            task_ids = batch.calibration_task_ids(available_tasks)
            # deactivate user with no calibration tasks
            if len(task_ids) == 0:
                deactivate_user(request.user, project)
                return redirect(index)


    if threshold == 0:
        task_ids = batch.available_task_ids_for(request.user)

    if skipped_ids:
        task_id = task_ids.exclude(id__in=skipped_ids).first()
        if not task_id:
            task_id = task_ids.filter(id__in=skipped_ids).first()
            if task_id:
                messages.info(request, 'Only previously skipped Tasks are available')

                # Once all remaining Tasks have been marked as skipped, we clear
                # their skipped status.  If we don't take this step, then a Task
                # cannot be skipped a second time.
                request.session['skipped_tasks_in_batch'][str(batch.id)] = []
                request.session.modified = True
    else:
        task_id = task_ids.first()

    return task_id


def sign_up(request):
    if request.method == "POST":
        form = SignUpForm(request.POST)
        if form.is_valid():
            user = form.save()
            # login(request, user, backend='django.contrib.auth.backends.ModelBackend')
            messages.success(request, "Successfully Signing up. please wait for your confirmation email.")
            return redirect(index)
        messages.error(request, form)
    form = SignUpForm()
    return render(request=request, template_name="turkle/SignUp.html", context={"register_form": form})
