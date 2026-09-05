def notifications(request):
    """
    Injects unread_notification_count and recent_notifications (latest 8
    unread, for the bell dropdown) into every template context. No-op for
    unauthenticated requests — cheap enough to run on every page since it's
    a single indexed query per request.
    """
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated:
        return {'unread_notification_count': 0, 'recent_notifications': []}

    qs = user.notifications.filter(is_read=False)
    return {
        'unread_notification_count': qs.count(),
        'recent_notifications': list(qs[:8]),
    }
