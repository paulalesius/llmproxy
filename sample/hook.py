# Pre-hook are run for request, post-hook are run after response
def hook(request_info, response_info=None):
    """
    request_info: {path, method, headers, body, query_params}
    response_info: {status_code, headers, body} (None i pre-hook)
    """
    if response_info is None:
        # Pre-hook - innan request skickas
        print(f"Incoming request to {request_info['path']}")
        # Kan modifiera request_info['headers'] etc.
    else:
        # Post-hook - efter response mottagen
        print(f"Response status: {response_info['status_code']}")
        # Kan logga response_info['body'] etc.

    return request_info, response_info
