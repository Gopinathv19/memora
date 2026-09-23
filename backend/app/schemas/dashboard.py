from pydantic import BaseModel


class TimeseriesPoint(BaseModel):
    """One day of a metric series. `date` is ISO `YYYY-MM-DD`, in server time."""

    date: str
    value: int


class MetricSeries(BaseModel):
    """A zero-filled daily series plus the totals that frame it.

    `current` counts the window itself and `previous` the identical span
    immediately before it, so the console can show a delta without a second
    request and without inventing the comparison client-side.
    """

    points: list[TimeseriesPoint]
    current: int
    previous: int


class DashboardMetrics(BaseModel):
    """Aggregated activity for the console dashboard, computed in SQL."""

    days: int
    sources: MetricSeries
    subjects: MetricSeries
    actors: MetricSeries
    sources_by_type: dict[str, int]
    storage_bytes: int
    largest_source_bytes: int
    last_source_at: str | None


class DashboardStats(BaseModel):
    """Live counts for the console dashboard. Never hard-coded on the client."""

    tenants: int
    applications: int
    actors: int
    subjects: int
    sources: int
    active_credentials: int
    sources_by_status: dict[str, int]


class AuthContextRead(BaseModel):
    """What an API credential resolves to. Returned by GET /api/v1/whoami."""

    credential_id: str
    credential_name: str
    application_id: str
    application_name: str
    application_slug: str
    tenant_id: str
    tenant_name: str
