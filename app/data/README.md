# Alert area boundaries

`alert_towns.geojson` contains Census municipality boundaries retrieved on
2026-10-22. Coordinates use WGS84 longitude/latitude, rounded to six decimal
places by the source service. No visual approximation or town-center radius
is used to determine warning membership.

Source: US Census Bureau TIGERweb Current.

- County subdivisions: https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/tigerWMS_Current/MapServer/22
- Incorporated places (Bellows Falls village): https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/tigerWMS_Current/MapServer/28

County-subdivision query: `(STATE='50' AND COUNTY='025') OR (STATE='33' AND COUNTY='005')`.
All Windham subdivisions are retained; New Hampshire selection is Keene,
Westmoreland, Walpole, Chesterfield and Hinsdale. Bellows Falls is queried as
`STATE='50' AND NAME LIKE '%Bellows%'` and assigned Windham county code `025`.
The response includes its Census GEOID. Refresh this snapshot when municipal
boundaries change.
