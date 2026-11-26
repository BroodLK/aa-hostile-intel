"""Read static sovereignty planning data from the installed EVE SDE.

This describes potential resources, not the live state of a sovereignty hub.
Missing resource records and nullable SDE fields are returned as None, not zero.
"""

# Third Party
from eve_sde.models import (
    Constellation,
    Planet,
    SolarSystem,
    SovereigntyUpgrade,
    Star,
    Stargate,
)


def _resource(body):
    resource = getattr(body, "resource", None)
    if resource is None:
        return None
    return {
        "power": resource.power,
        "workforce": resource.workforce,
        "reagent": {
            "type_id": resource.reagent_type_id,
            "amount_per_cycle": resource.reagent_amount_per_cycle,
            "cycle_period": resource.reagent_cycle_period,
            "secured_capacity": resource.reagent_secured_capacity,
            "unsecured_capacity": resource.reagent_unsecured_capacity,
        },
    }


def get_sov_planner_catalog():
    """Return the imported map and sovereignty upgrades as JSON-ready data.

    Resource totals are static potential, not connected hub resources. A total
    is unknown unless a star, at least one planet, and all their resource
    values for that dimension are present. An incomplete import cannot be
    detected, including missing gates or planets.
    """
    constellations = list(
        Constellation.objects.order_by("name", "id").values("id", "name")
    )
    systems = list(
        SolarSystem.objects.order_by("name", "id").values(
            "id", "name", "constellation_id", "security_status"
        )
    )
    bodies = {system["id"]: [] for system in systems}
    stars = set()
    planets = set()
    for star in Star.objects.select_related("resource").exclude(solar_system_id=None):
        if star.solar_system_id in bodies:
            stars.add(star.solar_system_id)
            bodies[star.solar_system_id].append(_resource(star))
    for planet in Planet.objects.select_related("resource").exclude(
        solar_system_id=None
    ):
        if planet.solar_system_id in bodies:
            planets.add(planet.solar_system_id)
            bodies[planet.solar_system_id].append(_resource(planet))

    neighbors = {system["id"]: set() for system in systems}
    for source, destination in (
        Stargate.objects.exclude(solar_system_id=None)
        .exclude(destination_id=None)
        .values_list("solar_system_id", "destination_id")
    ):
        if source in neighbors:
            neighbors[source].add(destination)

    for system in systems:
        system_id = system["id"]
        system["security"] = system.pop("security_status")
        system["neighbors"] = sorted(neighbors[system_id])
        for field in ("power", "workforce"):
            resources = bodies[system_id]
            system[field] = (
                sum(resource[field] for resource in resources)
                if system_id in stars
                and system_id in planets
                and all(
                    resource is not None and resource[field] is not None
                    for resource in resources
                )
                else None
            )

    upgrades = [
        {
            "id": upgrade.item_type_id,
            "name": upgrade.item_type.name,
            "description": upgrade.item_type.description,
            "published": upgrade.item_type.published,
            "power": upgrade.power_allocation,
            "workforce": upgrade.workforce_allocation,
            "power_production": upgrade.power_production,
            "workforce_production": upgrade.workforce_production,
            "mutually_exclusive_group": upgrade.mutually_exclusive_group,
            "fuel_type_id": upgrade.fuel_item_type_id,
            "hourly_upkeep": upgrade.hourly_upkeep,
            "startup_cost": upgrade.startup_cost,
        }
        for upgrade in SovereigntyUpgrade.objects.filter(item_type__published=True)
        .select_related("item_type")
        .order_by("item_type__name", "item_type_id")
    ]
    return {"constellations": constellations, "systems": systems, "upgrades": upgrades}


def get_sov_planner_data(constellation_id):
    """Return a JSON-serializable SDE snapshot, or None for an unknown constellation.

    Database errors propagate. Empty collections may indicate an incomplete SDE
    import; neither topology completeness nor live hub connections is implied.
    """
    constellation = Constellation.objects.filter(pk=constellation_id).first()
    if constellation is None:
        return None

    systems = list(
        SolarSystem.objects.filter(constellation=constellation).order_by("name", "id")
    )
    system_ids = [system.id for system in systems]
    planets_by_system = {system_id: [] for system_id in system_ids}
    for planet in (
        Planet.objects.filter(solar_system_id__in=system_ids)
        .select_related("resource", "item_type")
        .order_by("name", "id")
    ):
        planets_by_system[planet.solar_system_id].append(
            {
                "id": planet.id,
                "name": planet.name,
                "type_id": planet.item_type_id,
                "type_name": planet.item_type.name if planet.item_type else None,
                "resource": _resource(planet),
            }
        )

    stars = {
        star.solar_system_id: star
        for star in Star.objects.filter(solar_system_id__in=system_ids).select_related(
            "resource"
        )
    }
    upgrades = (
        SovereigntyUpgrade.objects.filter(item_type__published=True)
        .select_related("item_type")
        .order_by("item_type__name", "item_type_id")
    )
    return {
        "constellation": {
            "id": constellation.id,
            "name": constellation.name,
            "region_id": constellation.region_id,
        },
        "systems": [
            {
                "id": system.id,
                "name": system.name,
                "security_status": system.security_status,
                "planets": planets_by_system[system.id],
                "star": (
                    {"id": stars[system.id].id, "resource": _resource(stars[system.id])}
                    if system.id in stars
                    else None
                ),
            }
            for system in systems
        ],
        "upgrades": [
            {
                "type_id": upgrade.item_type_id,
                "name": upgrade.item_type.name,
                "description": upgrade.item_type.description,
                "published": upgrade.item_type.published,
                "power_allocation": upgrade.power_allocation,
                "power_production": upgrade.power_production,
                "workforce_allocation": upgrade.workforce_allocation,
                "workforce_production": upgrade.workforce_production,
                "mutually_exclusive_group": upgrade.mutually_exclusive_group,
                "fuel_type_id": upgrade.fuel_item_type_id,
                "hourly_upkeep": upgrade.hourly_upkeep,
                "startup_cost": upgrade.startup_cost,
            }
            for upgrade in upgrades
        ],
    }
