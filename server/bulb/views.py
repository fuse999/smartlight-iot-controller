import json

# JsonResponse returns JSON data back to the browser.
# HttpResponseBadRequest is used when the request data is invalid.
from django.http import JsonResponse, HttpResponseBadRequest

# render displays an HTML template,
# redirect sends the user to another URL,
# get_object_or_404 fetches an object or returns a 404 page if it does not exist.
from django.shortcuts import render, redirect, get_object_or_404

# require_POST makes sure a view only accepts POST requests.
from django.views.decorators.http import require_POST

# ensure_csrf_cookie makes sure the browser receives a CSRF cookie.
# This is helpful for pages that later use JavaScript fetch POST requests.
from django.views.decorators.csrf import ensure_csrf_cookie

# permission_required checks whether the logged-in user has a specific permission.
from django.contrib.auth.decorators import permission_required

# login signs a user in after successful registration.
from django.contrib.auth import login

# Permission lets us assign a specific permission to a user account.
from django.contrib.auth.models import Permission

# timezone lets us work with Django's current active timezone.
from django.utils import timezone

# Import forms used in the pages.
from .forms import LightScheduleForm, RegisterForm

# Import the LightSchedule model so we can create, read, update, and delete schedules.
from .models import LightSchedule

# Import helper/service functions that interact with the bulb state and schedule timing.
from .services import get_state, set_light, set_brightness, refresh_next_run


# Displays the public home page.
# This page does not require login or permission.
def home_view(request):
    return render(request, "home.html")


# Displays the manual control page.
# Instead of blocking access immediately, this view checks whether the
# user is allowed to control the bulb and passes that result to the template.
# The template then decides whether to show:
# 1. an account access message,
# 2. an access restricted message, or
# 3. the actual controls.
def control_page(request):
    state = get_state()
    can_control = request.user.is_authenticated and request.user.has_perm("bulb.can_control_bulb")

    return render(request, "bulb/control.html", {
        "state": state,
        "can_control": can_control,
    })


# This API endpoint changes the bulb power state.
# It only allows POST requests, and the user must have bulb control permission.
@permission_required("bulb.can_control_bulb", raise_exception=True)
@require_POST
def set_power_api(request):
    try:
        # Read the JSON body sent from JavaScript.
        payload = json.loads(request.body.decode("utf-8"))

        # Expect a boolean-like value under the key "on".
        on = bool(payload["on"])
    except Exception:
        # If the JSON is missing or invalid, return a 400 Bad Request.
        return HttpResponseBadRequest("Invalid JSON payload. Expected: {'on': true/false}")

    # Update the saved bulb state through the service layer.
    state = set_light(on)

    # Return the updated state as JSON so the page can update without reloading.
    return JsonResponse({
        "ok": True,
        "is_on": state.is_on,
        "updated_at": state.updated_at.isoformat(),
    })


# This API endpoint changes the bulb brightness.
# Like the power endpoint, it only allows POST and requires permission.
@permission_required("bulb.can_control_bulb", raise_exception=True)
@require_POST
def set_brightness_api(request):
    try:
        # Read the JSON body and convert brightness to an integer.
        payload = json.loads(request.body.decode("utf-8"))
        brightness = int(payload["brightness"])
    except Exception:
        return HttpResponseBadRequest("Invalid JSON payload. Expected: {'brightness': 0-100}")

    # Update brightness through the service layer.
    state = set_brightness(brightness)

    # Send the updated brightness back to the browser.
    return JsonResponse({
        "ok": True,
        "brightness": state.brightness,
        "updated_at": state.updated_at.isoformat(),
    })


# This endpoint returns the current bulb state as JSON.
# It is useful for polling from JavaScript so the page can detect state changes.
def light_state_api(request):
    state = get_state()

    return JsonResponse({
        "is_on": state.is_on,
        "brightness": state.brightness,
        "updated_at": state.updated_at.isoformat() if state.updated_at else None,
    })


# Displays the schedules page and handles creation of new schedules.
# The page itself is viewable even if the user is not authorized,
# but only users with the correct permission are allowed to create schedules.
def schedules_page(request):
    can_control = request.user.is_authenticated and request.user.has_perm("bulb.can_control_bulb")

    if request.method == "POST":
        # If someone submits the form without permission,
        # send them back to the dashboard instead of allowing schedule creation.
        if not can_control:
            return redirect("bulb_dashboard")

        # Bind the submitted POST data to the schedule form.
        form = LightScheduleForm(request.POST)

        if form.is_valid():
            # commit=False lets us fill in extra fields before saving the model.
            schedule = form.save(commit=False)

            # Clear runtime-related fields when a new schedule is first created.
            schedule.claimed_at = None
            schedule.last_run_at = None

            # Save the timezone currently active in Django.
            schedule.timezone_name = timezone.get_current_timezone_name()

            # Save the schedule to the database.
            schedule.save()

            # Calculate and store the next time this schedule should run.
            refresh_next_run(schedule)

            # After success, redirect back to the schedules page.
            return redirect("bulb_schedules")
    else:
        # For a normal GET request, create a blank form.
        form = LightScheduleForm()

    # Only show the schedule list to users with permission.
    # Other users see the page shell and access messaging instead.
    schedules = LightSchedule.objects.order_by("next_run_at", "id") if can_control else []

    return render(request, "bulb/schedules.html", {
        "form": form,
        "schedules": schedules,
        "can_control": can_control,
    })


# Displays the main dashboard page.
# ensure_csrf_cookie makes sure the browser receives the CSRF cookie,
# which is needed because this page uses JavaScript fetch POST requests later.
@ensure_csrf_cookie
def dashboard_page(request):
    state = get_state()

    # Determine whether the current user is both logged in and authorized.
    can_control = request.user.is_authenticated and request.user.has_perm("bulb.can_control_bulb")

    if request.method == "POST":
        # If a user without permission submits the schedule form,
        # redirect them back to the dashboard.
        if not can_control:
            return redirect("bulb_dashboard")

        # Bind submitted form data.
        form = LightScheduleForm(request.POST)

        if form.is_valid():
            # Build the schedule object without saving yet.
            schedule = form.save(commit=False)

            # Initialize runtime tracking fields.
            schedule.claimed_at = None
            schedule.last_run_at = None

            # Save the currently active timezone.
            schedule.timezone_name = timezone.get_current_timezone_name()

            # Save schedule to the database.
            schedule.save()

            # Compute the next run time after saving.
            refresh_next_run(schedule)

            # Redirect after successful form submission.
            return redirect("bulb_dashboard")
    else:
        # For a normal page load, give the template a blank form.
        form = LightScheduleForm()

    # Only show schedules to users who are allowed to manage them.
    schedules = LightSchedule.objects.order_by("next_run_at", "id") if can_control else []

    return render(request, "bulb/dashboard.html", {
        "state": state,
        "form": form,
        "schedules": schedules,
        "register_form": RegisterForm(),
        "can_control": can_control,
    })


# Toggles a schedule between enabled and disabled.
# Only authorized users can do this, and the request must be POST.
@permission_required("bulb.can_control_bulb", raise_exception=True)
@require_POST
def toggle_schedule(request, schedule_id):
    # Load the schedule by ID or return a 404 if it does not exist.
    schedule = get_object_or_404(LightSchedule, id=schedule_id)

    # Flip the enabled flag.
    schedule.enabled = not schedule.enabled

    if schedule.enabled:
        # If the schedule is being turned back on,
        # clear claimed_at and then recompute the next run.
        schedule.claimed_at = None
        schedule.save(update_fields=["enabled", "claimed_at"])
        refresh_next_run(schedule)
    else:
        # If the schedule is being disabled,
        # just save the new enabled state and clear claimed_at.
        schedule.claimed_at = None
        schedule.save(update_fields=["enabled", "claimed_at"])

    # Redirect back to the page the form came from, if provided.
    # Otherwise default to the dashboard.
    return redirect(request.POST.get("next") or "bulb_dashboard")


# Deletes a schedule from the database.
# Only authorized users can do this, and the request must be POST.
@permission_required("bulb.can_control_bulb", raise_exception=True)
@require_POST
def delete_schedule(request, schedule_id):
    # Load the schedule or return 404 if not found.
    schedule = get_object_or_404(LightSchedule, id=schedule_id)

    # Remove it from the database.
    schedule.delete()

    # Return the user to the previous page if provided.
    return redirect(request.POST.get("next") or "bulb_dashboard")


# Stores the user's chosen timezone in the session.
# This lets the application remember the timezone across requests.
@require_POST
def set_timezone_api(request):
    try:
        # Read the JSON body and extract the timezone name.
        payload = json.loads(request.body.decode("utf-8"))
        tzname = payload["timezone"]
    except Exception:
        return HttpResponseBadRequest("Invalid JSON payload. Expected: {'timezone': 'Area/City'}")

    # Save the timezone name in the session.
    request.session["django_timezone"] = tzname

    return JsonResponse({
        "ok": True,
        "timezone": tzname,
    })


# Handles user registration.
# This view expects a POST request from the registration form.
def register_view(request):
    # If someone visits this URL directly with GET,
    # just send them back to the dashboard.
    if request.method != "POST":
        return redirect("bulb_dashboard")

    # Bind the submitted registration form data.
    register_form = RegisterForm(request.POST)

    if register_form.is_valid():
        # Save the new user account.
        user = register_form.save()

        try:
            # Attempt to give the new user bulb control permission automatically.
            perm = Permission.objects.get(codename="can_control_bulb")
            user.user_permissions.add(perm)
        except Permission.DoesNotExist:
            # If the permission has not been created, skip this step.
            pass

        # Log the new user in immediately after registration.
        login(request, user)

        # Redirect to the requested next page, or default to dashboard.
        next_url = request.POST.get("next") or "/dashboard/"
        return redirect(next_url)

    # If the registration form is invalid, rebuild the dashboard context
    # so the template can re-open the modal and show validation errors.
    state = get_state()
    can_control = request.user.is_authenticated and request.user.has_perm("bulb.can_control_bulb")
    schedules = LightSchedule.objects.order_by("next_run_at", "id") if can_control else []

    return render(request, "bulb/dashboard.html", {
        "state": state,
        "form": LightScheduleForm(),
        "schedules": schedules,
        "register_form": register_form,
        "can_control": can_control,
        "open_register_modal": True,
    })