import numpy

from catalog_server.datasources import ArraySource
from catalog_server.in_memory_catalog import Catalog


# Build Catalog of Catalogs.
subcatalogs = {}
for name, size, fruit, animal in zip(
    ["tiny", "small", "medium", "large"],
    [3, 100, 1000, 10_000],
    ["apple", "banana", "orange", "grape"],
    ["bird", "cat", "dog", "penguin"],
):
    subcatalogs[name] = Catalog(
        {
            k: ArraySource(v * numpy.ones((size, size)))
            for k, v in zip(["ones", "twos", "threes"], [1, 2, 3])
        },
        metadata={"fruit": fruit, "animal": animal},
    )
catalog = Catalog(subcatalogs)
