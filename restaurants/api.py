"""
Restaurant endpoints, loaded by server/app.py.

    GET /api/restaurants/config                       city + demo-mode info
    GET /api/restaurants/search?lat=&lon=&radius=     ranked restaurants
"""

from restaurants import service
from server import cities
from server.router import ApiError


def register(router):
    router.get("/api/restaurants/config", get_config)
    router.get("/api/restaurants/search", get_search)


def _city(request):
    try:
        return cities.get(request.query.get("city"))
    except KeyError as e:
        raise ApiError(400, e.args[0])


def get_config(request):
    city = _city(request)
    m = service.mode()
    return {
        "city": {k: city.get(k) for k in ("key", "name", "center", "center_label", "zoom", "bounds")},
        "demo": m["demo"],
        "demo_reason": m["reason"],
        "radius": {"min": service.MIN_RADIUS_M, "max": service.MAX_RADIUS_M,
                   "default": service.DEFAULT_RADIUS_M},
    }


def get_search(request):
    city = _city(request)
    lon = request.number("lon", default=city["center"][0], low=-180, high=180)
    lat = request.number("lat", default=city["center"][1], low=-90, high=90)
    radius = request.number("radius", default=service.DEFAULT_RADIUS_M,
                            low=service.MIN_RADIUS_M, high=service.MAX_RADIUS_M)
    if not cities.contains(city, lon, lat):
        raise ApiError(400, "That point is outside %s. Choose a point inside the city."
                       % city["name"])
    return service.search(lat, lon, radius, city["key"])
