import uuid
from pickle import loads, dumps
from .proxy import conn

class DelayedResult(object):
    def __init__(self, key):
        self.key = key
        self._rv = None

    @property
    def return_value(self):
        if self._rv is None:
            rv = conn.get(self.key)
            if rv is not None:
                # cache the result
                self._rv = loads(rv)
        return self._rv

class Job(object):
    """A Job is just a convenient datastructure to pass around job (meta) data.
    """

    @classmethod
    def unpickle(cls, pickle_data):
        job_tuple = loads(pickle_data)
        return Job(job_tuple)

    def __init__(self, job_tuple, origin=None):
        self.func, self.args, self.kwargs, self.rv_key = job_tuple
        self.origin = origin

    def perform(self):
        """Invokes the job function with the job arguments.
        """
        return self.func(*self.args, **self.kwargs)


class Queue(object):
    redis_queue_namespace_prefix = 'rq:'

    @classmethod
    def from_queue_key(cls, queue_key):
        """Returns a Queue instance, based on the naming conventions for naming
        the internal Redis keys.  Can be used to reverse-lookup Queues by their
        Redis keys.
        """
        prefix = cls.redis_queue_namespace_prefix
        if not queue_key.startswith(prefix):
            raise ValueError('Not a valid RQ queue key: %s' % (queue_key,))
        name = queue_key[len(prefix):]
        return Queue(name)

    def __init__(self, name='default'):
        prefix = self.redis_queue_namespace_prefix
        self.name = name
        self._key = '%s%s' % (prefix, name)

    @property
    def key(self):
        return self._key

    @property
    def empty(self):
        return self.count == 0

    @property
    def messages(self):
        return conn.lrange(self.key, 0, -1)

    @property
    def count(self):
        return conn.llen(self.key)

    def enqueue(self, job, *args, **kwargs):
        rv_key = '%s:result:%s' % (self.key, str(uuid.uuid4()))
        if job.__module__ == '__main__':
            raise ValueError('Functions from the __main__ module cannot be processed by workers.')
        message = dumps((job, args, kwargs, rv_key))
        conn.rpush(self.key, message)
        return DelayedResult(rv_key)

    def dequeue(self):
        blob = conn.lpop(self.key)
        if blob is None:
            return None
        job = Job.unpickle(blob)
        job.origin = self
        return job

    @classmethod
    def _lpop_any(cls, queue_keys):
        """Helper method.  You should not call this directly.

        Redis' BLPOP command takes multiple queue arguments, but LPOP can only
        take a single queue.  Therefore, we need to loop over all queues
        manually, in order, and return None if no more work is available.
        """
        for queue_key in queue_keys:
            blob = conn.lpop(queue_key)
            if blob is not None:
                return (queue_key, blob)
        return None

    @classmethod
    def dequeue_any(cls, queues, blocking):
        queue_keys = map(lambda q: q.key, queues)
        if blocking:
            queue_key, blob = conn.blpop(queue_keys)
        else:
            redis_result = cls._lpop_any(queue_keys)
            if redis_result is None:
                return None
            queue_key, blob = redis_result

        job = Job.unpickle(blob)
        queue = Queue.from_queue_key(queue_key)
        job.origin = queue
        return job


    def __str__(self):
        return self.name
