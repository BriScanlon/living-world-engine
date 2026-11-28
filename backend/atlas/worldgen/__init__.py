"""Native world generation (epic #45): stages that build a campaign region in Azgaar's Full JSON shape.

Each stage is a module with pure functions over a cell mesh (numpy + shapely only; no scipy or noise libraries):

* ``mesh``: Voronoi cells from a jittered lattice (phase 1, #39);
* ``terrain``: heightmap, sea level and coastline (phase 1, #39);
* later: water (#40), climate (#41), peoples and polities (#42), routes (#43).

Until those land, ``atlas.sample_world.generate(..., mesh="voronoi")`` runs these stages and keeps its own for the
rest, so every phase ships on its own and the importer, tiles, atlas and travel stay unchanged.
"""
