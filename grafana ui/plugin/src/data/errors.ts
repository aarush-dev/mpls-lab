// Error helpers shared by DataClient implementations. MockDataClient never throws these in
// practice (fixtures are always well-formed), but HttpDataClient (M3+) will use `normalizeError`
// to turn FastAPI's `{detail}` error body into the `ApiError` shape defined in `types.ts`.

import type { ApiError } from './types';

/** Build a well-formed ApiError. */
export function makeApiError(
  status: number,
  code: string,
  message: string,
  opts?: { retryable?: boolean; requestId?: string }
): ApiError {
  return {
    status,
    code,
    message,
    retryable: opts?.retryable ?? status >= 500,
    requestId: opts?.requestId,
  };
}

/**
 * Normalize an arbitrary thrown value (fetch rejection, FastAPI `{detail}` body, or already an
 * ApiError) into an ApiError. Used by HttpDataClient; kept here now so the shape is settled before
 * that client exists.
 */
export function normalizeError(e: unknown): ApiError {
  if (isApiError(e)) {
    return e;
  }

  if (e instanceof Error) {
    return makeApiError(0, 'network_error', e.message, { retryable: true });
  }

  if (typeof e === 'object' && e !== null) {
    const body = e as { detail?: unknown; status?: unknown };
    if (typeof body.detail === 'string') {
      const status = typeof body.status === 'number' ? body.status : 500;
      return makeApiError(status, 'api_error', body.detail, { retryable: status >= 500 });
    }
  }

  return makeApiError(0, 'unknown_error', 'An unknown error occurred', { retryable: false });
}

function isApiError(e: unknown): e is ApiError {
  return (
    typeof e === 'object' &&
    e !== null &&
    typeof (e as ApiError).status === 'number' &&
    typeof (e as ApiError).code === 'string' &&
    typeof (e as ApiError).message === 'string' &&
    typeof (e as ApiError).retryable === 'boolean'
  );
}
