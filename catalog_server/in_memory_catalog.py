import collections.abc
import itertools

from catalog_server.queries import DictView, QueryTranslationRegistry, Text


class Catalog(collections.abc.Mapping):

    __slots__ = ("_mapping", "_metadata")

    # Define classmethods for managing what queries this Catalog knows.
    __query_registry = QueryTranslationRegistry()
    register_query = __query_registry.register
    register_query_lazy = __query_registry.register_lazy

    def __init__(self, mapping, metadata=None):
        self._mapping = mapping
        self._metadata = metadata or {}

    @property
    def metadata(self):
        "Metadata about this Catalog."
        # Ensure this is immutable (at the top level) to help the user avoid
        # getting the wrong impression that editing this would update anything
        # persistent.
        return DictView(self._metadata)

    def __repr__(self):
        return f"<{type(self).__name__}({set(self)!r})>"

    def __getitem__(self, key):
        return self._mapping[key]

    def __iter__(self):
        yield from self._mapping

    def __len__(self):
        return len(self._mapping)

    def search(self, query):
        """
        Return a Catalog with a subset of the mapping.
        """
        return self.__query_registry(query, self)

    def _keys_slice(self, start, stop):
        yield from itertools.islice(
            self._mapping.keys(),
            start,
            stop,
        )

    def _items_slice(self, start, stop):
        # A goal of this implementation is to avoid iterating over
        # self._mapping.values() because self._mapping may be a LazyMap which
        # only constructs its values at access time. With this in mind, we
        # identify the key(s) of interest and then only access those values.
        yield from ((key, self._mapping[key]) for key in self._keys_slice(start, stop))

    def _item_by_index(self, index):
        if index >= len(self):
            raise IndexError(f"index {index} out of range for length {len(self)}")
        key = next(itertools.islice(self._mapping.keys(), index, 1 + index))
        return (key, self._mapping[key])

    @property
    def keys_indexer(self):
        return CatalogKeysSequence(self)

    @property
    def items_indexer(self):
        return CatalogItemsSequence(self)

    @property
    def values_indexer(self):
        return CatalogValuesSequence(self)


def _slice_to_interval(index):
    "Check that slice is supported; then return (start, stop)."
    if index.start is None:
        start = 0
    elif index.start < 0:
        raise NotImplementedError
    else:
        start = index.start
    if index.stop is not None:
        if index.stop < 0:
            raise NotImplementedError
    stop = index.stop
    return start, stop


def _compose_intervals(a, b):
    a_start, a_stop = a
    b_start, b_stop = b
    if a_start is None:
        if b_start is None:
            start = 0
        else:
            start = b_start
    else:
        if b_start is None:
            start = a_start
        else:
            start = a_start + b_start
    if a_stop is None:
        if b_stop is None:
            stop = None
        else:
            stop = b_stop + a_start
    else:
        if b_stop is None:
            stop = a_stop
        else:
            stop = min(a_stop, b_stop + a_start)
    return start, stop


class CatalogBaseSequence(collections.abc.Sequence):
    "Base class for Keys, Values, Items Sequences."

    def __init__(self, ancestor, start=0, stop=None):
        self._ancestor = ancestor
        self._start = int(start or 0)
        if stop is not None:
            stop = int(stop)
        self._stop = stop

    def __repr__(self):
        return f"<{type(self).__name__}({list(self)!r})>"

    def __len__(self):
        len_ = len(self._ancestor) - self._start
        if self._stop is not None and (len_ > (self._stop - self._start)):
            return self._stop - self._start
        else:
            return len_

    def __getitem__(self, index):
        "Subclasses handle the case of an integer index."
        if isinstance(index, slice):
            start, stop = _slice_to_interval(index)
            # Return another instance of type(self), progpagating forward a
            # reference to self and the sub-slicing specified by index.
            return type(self)(self, start, stop)
        else:
            raise TypeError(f"{index} must be an int or slice, not {type(index)}")

    def _item_by_index(self, index):
        # Recurse
        return self._ancestor._item_by_index(index + self._start)

    def _items_slice(self, start, stop):
        # Recurse
        agg_start, agg_stop = _compose_intervals(
            (self._start, self._stop), (start, stop)
        )
        return self._ancestor._items_slice(agg_start, agg_stop)

    def _keys_slice(self, start, stop):
        # Recurse
        agg_start, agg_stop = _compose_intervals(
            (self._start, self._stop), (start, stop)
        )
        return self._ancestor._keys_slice(agg_start, agg_stop)


class CatalogKeysSequence(CatalogBaseSequence):
    def __iter__(self):
        return self._ancestor._keys_slice(self._start, self._stop)

    def __getitem__(self, index):
        if isinstance(index, int):
            key, _value = self._item_by_index(index)
            return key
        return super().__getitem__(index)


class CatalogItemsSequence(CatalogBaseSequence):
    def __iter__(self):
        return self._ancestor._items_slice(self._start, self._stop)

    def __getitem__(self, index):
        if isinstance(index, int):
            return self._item_by_index(index)
        return super().__getitem__(index)


class CatalogValuesSequence(CatalogBaseSequence):
    def __iter__(self):
        # Extract just the value for the iterable of (key, value) items.
        return (
            value
            for _key, value in self._ancestor._items_slice(self._start, self._stop)
        )

    def __getitem__(self, index):
        if isinstance(index, int):
            # Extract just the value from the item.
            _key, value = self._item_by_index(index)
            return value
        return super().__getitem__(index)


def walk_string_values(tree, node=None):
    """
    >>> list(
    ...     walk_string_values(
    ...         {'a': {'b': {'c': 'apple', 'd': 'banana'},
    ...          'e': ['cat', 'dog']}, 'f': 'elephant'}
    ...     )
    ... )
    ['apple', 'banana', 'cat', 'dog', 'elephant']
    """
    if node is None:
        for node in tree:
            yield from walk_string_values(tree, node)
    else:
        value = tree[node]
        if isinstance(value, str):
            yield value
        elif hasattr(value, "items"):
            for k, v in value.items():
                yield from walk_string_values(value, k)
        elif isinstance(value, collections.abc.Iterable):
            for item in value:
                if isinstance(item, str):
                    yield item


def full_text_search(query, catalog):
    matches = {}
    query_words = set(query.text.lower().split())
    for key, value in catalog.items():
        words = set(
            word
            for s in walk_string_values(value.metadata)
            for word in s.lower().split()
        )
        # Note that `not set.isdisjoint` is faster than `set.intersection`. At
        # the C level, `isdisjoint` loops over the set until it finds one match,
        # and then bails, whereas `intersection` proceeds to find all matches.
        if not words.isdisjoint(query_words):
            matches[key] = value
    return type(catalog)(matches)


Catalog.register_query(Text, full_text_search)
