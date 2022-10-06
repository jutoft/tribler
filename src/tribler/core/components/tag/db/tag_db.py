import datetime
import logging
from enum import IntEnum
from typing import Callable, Iterable, List, Optional, Set

from pony import orm
from pony.orm.core import Entity
from pony.utils import between

from tribler.core.components.tag.community.tag_payload import StatementOperation
from tribler.core.utilities.pony_utils import get_or_create

CLOCK_START_VALUE = 0

PUBLIC_KEY_FOR_AUTO_GENERATED_TAGS = b'auto_generated'

SHOW_THRESHOLD = 1
HIDE_THRESHOLD = -2


class Operation(IntEnum):
    ADD = 1
    REMOVE = 2


class Predicate(IntEnum):
    HAS_CONTRIBUTOR = 1
    HAS_COVERAGE = 2
    HAS_CREATOR = 3
    HAS_DATE = 4
    HAS_DESCRIPTION = 5
    HAS_FORMAT = 6
    HAS_IDENTIFIER = 7
    HAS_LANGUAGE = 8
    HAS_PUBLISHER = 9
    HAS_RELATION = 10
    HAS_RIGHTS = 11
    HAS_SOURCE = 12
    HAS_SUBJECT = 13
    HAS_TITLE = 14
    HAS_TYPE = 15

    HAS_TAG = 101
    HAS_TORRENT = 102

class TagDatabase:
    def __init__(self, filename: Optional[str] = None, *, create_tables: bool = True, **generate_mapping_kwargs):
        self.instance = orm.Database()
        self.define_binding(self.instance)
        self.instance.bind('sqlite', filename or ':memory:', create_db=True)
        generate_mapping_kwargs['create_tables'] = create_tables
        self.instance.generate_mapping(**generate_mapping_kwargs)
        self.logger = logging.getLogger(self.__class__.__name__)

    @staticmethod
    def define_binding(db):
        class Peer(db.Entity):
            id = orm.PrimaryKey(int, auto=True)
            public_key = orm.Required(bytes, unique=True)
            added_at = orm.Optional(datetime.datetime, default=datetime.datetime.utcnow)
            operations = orm.Set(lambda: StatementOp)

        class Statement(db.Entity):
            id = orm.PrimaryKey(int, auto=True)

            subject = orm.Required(lambda: Resource)
            predicate = orm.Required(int, default=101, index=True)  # default is the 'HAS_TAG' predicate
            object = orm.Required(lambda: Resource)

            operations = orm.Set(lambda: StatementOp)

            added_count = orm.Required(int, default=0)
            removed_count = orm.Required(int, default=0)

            local_operation = orm.Optional(int)  # in case user don't (or do) want to see it locally

            orm.composite_key(subject, predicate, object)

            @property
            def score(self):
                return self.added_count - self.removed_count

            def update_counter(self, operation: Operation, increment: int = 1, is_local_peer: bool = False):
                """ Update Statement's counter
                Args:
                    operation: Resource operation
                    increment:
                    is_local_peer: The flag indicates whether do we performs operations from a local user or from
                        a remote user. In case of the local user, his operations will be considered as
                        authoritative for his (only) local Tribler instance.

                Returns:
                """
                if is_local_peer:
                    self.local_operation = operation
                if operation == Operation.ADD:
                    self.added_count += increment
                if operation == Operation.REMOVE:
                    self.removed_count += increment

        class Resource(db.Entity):
            id = orm.PrimaryKey(int, auto=True)
            name = orm.Required(str, unique=True)

            subject_statements = orm.Set(lambda: Statement, reverse="subject")
            object_statements = orm.Set(lambda: Statement, reverse="object")

        class StatementOp(db.Entity):
            id = orm.PrimaryKey(int, auto=True)

            statement = orm.Required(lambda: Statement)
            peer = orm.Required(lambda: Peer)

            operation = orm.Required(int)
            clock = orm.Required(int)
            signature = orm.Required(bytes)
            updated_at = orm.Required(datetime.datetime, default=datetime.datetime.utcnow)
            auto_generated = orm.Required(bool, default=False)

            orm.composite_key(statement, peer)

    def add_operation(self, operation: StatementOperation, signature: bytes, is_local_peer: bool = False,
                      is_auto_generated: bool = False, counter_increment: int = 1) -> bool:
        """ Add the operation that will be applied to a statement.
        Args:
            operation: the class describes the adding operation
            signature: the signature of the operation
            is_local_peer: local operations processes differently than remote operations. They affects
                `Statement.local_operation` field which is used in `self.get_tags()` function.
            is_auto_generated: the indicator of whether this resource was generated automatically or not
            counter_increment: the counter or "numbers" of adding operations

        Returns: True if the operation has been added/updated, False otherwise.
        """
        self.logger.debug(f'Add operation. {operation.subject} "{operation.predicate}" {operation.object}')
        peer = get_or_create(self.instance.Peer, public_key=operation.creator_public_key)
        subject = get_or_create(self.instance.Resource, name=operation.subject)
        obj = get_or_create(self.instance.Resource, name=operation.object)
        statement = get_or_create(self.instance.Statement, subject=subject, predicate=operation.predicate, object=obj)
        op = self.instance.StatementOp.get_for_update(statement=statement, peer=peer)

        if not op:  # then insert
            self.instance.StatementOp(statement=statement, peer=peer, operation=operation.operation,
                                      clock=operation.clock, signature=signature, auto_generated=is_auto_generated)
            statement.update_counter(operation.operation, increment=counter_increment, is_local_peer=is_local_peer)
            return True

        # if it is a message from the past, then return
        if operation.clock <= op.clock:
            return False

        # To prevent endless incrementing of the operation, we apply the following logic:

        # 1. Decrement previous operation
        statement.update_counter(op.operation, increment=-counter_increment, is_local_peer=is_local_peer)
        # 2. Increment new operation
        statement.update_counter(operation.operation, increment=counter_increment, is_local_peer=is_local_peer)

        # 3. Update the operation entity
        op.set(operation=operation.operation, clock=operation.clock, signature=signature,
               updated_at=datetime.datetime.utcnow(), auto_generated=is_auto_generated)
        return True

    def add_auto_generated(self, subject: str, predicate: Predicate, obj: str):
        operation = StatementOperation(
            subject=subject,
            predicate=predicate,
            object=obj,
            operation=Operation.ADD,
            clock=CLOCK_START_VALUE,
            creator_public_key=PUBLIC_KEY_FOR_AUTO_GENERATED_TAGS,
        )

        self.add_operation(operation, signature=b'', is_local_peer=False, is_auto_generated=True,
                           counter_increment=SHOW_THRESHOLD)

    @staticmethod
    def _show_condition(statement):
        """This function determines show condition for the torrent_tag"""
        return statement.local_operation == Operation.ADD.value or \
               not statement.local_operation and statement.score >= SHOW_THRESHOLD

    def _get_resources(self, resource: str, condition: Callable[[], bool], predicate: Predicate) -> List[str]:
        """ Get resources that satisfy a given condition.
        """
        resource_entity = self.instance.Resource.get(name=resource)
        if not resource_entity:
            return []

        query = (
            resource_entity.subject_statements
            .select(condition)
            .filter(lambda statement: statement.predicate == predicate.value)
        )
        query = query.order_by(lambda statement: orm.desc(statement.score))
        query = orm.select(statement.object.name for statement in query)
        return list(query)

    def get_objects(self, subject: str, predicate: Predicate) -> List[str]:
        """ Get resources that satisfies given subject and predicate.
        """
        self.logger.debug(f'Get resources for {subject} with {predicate}')

        return self._get_resources(subject, self._show_condition, predicate)

    def get_suggestions(self, subject: str, predicate: Predicate) -> List[str]:
        """Get all suggestions for a particular subject.
        """
        self.logger.debug(f"Getting suggestions for {subject} with {predicate}")

        def show_suggestions_condition(statement):
            return not statement.local_operation and \
                   between(statement.score, HIDE_THRESHOLD + 1, SHOW_THRESHOLD - 1)

        return self._get_resources(subject, show_suggestions_condition, predicate)

    def get_subjects(self, objects: Set[str], predicate: Predicate) -> List[bytes]:
        return []
    #     """Get list of subjects that could be linked back to the objects.
    #     Only resources with condition `_show_condition` will be returned.
    #     In the case that the object set contains more than one tag,
    #     only subjects that contain all `objects` will be returned.
    #     """
    #     # FIXME: Ask @kozlovsky how to do it in a proper way
    #     objects_entities = select(r for r in self.instance.Resource if r.name in objects).fetch()
    #     if not objects_entities:
    #         return []
    #
    #     query_results = select(
    #         torrent.infohash for torrent in self.instance.Torrent
    #         if not exists(
    #             tag for tag in self.instance.Tag
    #             if tag.name in tags and not exists(
    #                 torrent_tag for torrent_tag in self.instance.TorrentTag
    #                 if torrent_tag.subject == torrent
    #                 and torrent_tag.object == tag
    #                 and self._show_condition(torrent_tag)
    #                 and torrent_tag.predicate == relation.value
    #             )
    #         )
    #     ).fetch()
    #     return query_results

    def get_clock(self, operation: StatementOperation) -> int:
        """ Get the clock (int) of operation.
        """
        peer = self.instance.Peer.get(public_key=operation.creator_public_key)
        subject = self.instance.Resource.get(name=operation.subject)
        obj = self.instance.Resource.get(name=operation.object)
        if not subject or not obj or not peer:
            return CLOCK_START_VALUE

        statement = self.instance.Statement.get(subject=subject, object=obj, predicate=operation.predicate)
        if not statement:
            return CLOCK_START_VALUE

        op = self.instance.StatementOp.get(statement=statement, peer=peer)
        return op.clock if op else CLOCK_START_VALUE

    def get_operations_for_gossip(self, time_delta, count: int = 10) -> Iterable[Entity]:
        """ Get random operations from the DB that older than time_delta.

        Args:
            time_delta: a dictionary for `datetime.timedelta`
            count: a limit for a resulting query
        """
        updated_at = datetime.datetime.utcnow() - datetime.timedelta(**time_delta)
        return self._get_random_operations_by_condition(
            condition=lambda so: so.updated_at <= updated_at and not so.auto_generated,
            count=count
        )

    def shutdown(self) -> None:
        self.instance.disconnect()

    def _get_random_operations_by_condition(self, condition: Callable[[Entity], bool], count: int = 5,
                                            attempts: int = 100) -> Set[Entity]:
        operations = set()
        for _ in range(attempts):
            if len(operations) == count:
                return operations

            random_operations_list = self.instance.StatementOp.select_random(1)
            if random_operations_list:
                operation = random_operations_list[0]
                if condition(operation):
                    operations.add(operation)

        return operations
