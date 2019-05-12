# -*- coding: utf-8 -*-
from __future__ import unicode_literals, division
import re
import datetime

STRING = str


class SchemaError(Exception):

    """Error during Schema validation."""

    def __init__(self, autos, errors=None):
        self.autos = autos if type(autos) is list else [autos]
        self.errors = errors if type(errors) is list else [errors]
        Exception.__init__(self, self.code)

    @property
    def code(self):
        def uniq(seq):
            seen = set()
            seen_add = seen.add
            return [x for x in seq if x not in seen and not seen_add(x)]
        a = uniq(i for i in self.autos if i is not None)
        e = uniq(i for i in self.errors if i is not None)
        if e:
            return '\n'.join(e)
        return '\n'.join(a)


def handle_default(init):

    """Add default handling to __init__ method; meant for decorators"""

    def init2(self, *args, **kw):
        # get default from the ``default`` keyword argument
        if 'default' in kw:
            self.default = kw['default']
            del(kw['default'])
        # if auto_default is set, get default from first argument
        elif hasattr(self, 'auto_default') and self.auto_default:
            self.default = args[0]
            if hasattr(self.default, 'default'):
                self.default = self.default.default
            elif type(self.default) == type:
                self.default = self.default()
        # normal init
        init(self, *args, **kw)
        # validate default
        if hasattr(self, 'default'):
            try:
                self.default = self.validate(self.default)
            except SchemaError:
                raise ValueError('%s does not validate its default: %s' % (
                    self, self.default))
    return init2


class And(object):

    @handle_default
    def __init__(self, *args, **kw):
        self._args = args
        assert len(args)
        assert list(kw) in (['error'], [])
        self._error = kw.get('error')

    def __repr__(self):
        return '%s(%s)' % (self.__class__.__name__,
                           ', '.join(repr(a) for a in self._args))

    def validate(self, data):
        for s in [Schema(s, error=self._error) for s in self._args]:
            data = s.validate(data)
        return data


class Or(And):

    def validate(self, data):
        x = SchemaError([], [])
        for s in [Schema(s, error=self._error) for s in self._args]:
            try:
                return s.validate(data)
            except SchemaError as _x:
                x = _x
        raise SchemaError(['%r did not validate %r' % (self, data)] + x.autos,
                          [self._error] + x.errors)


class EnumVal(object):

    @handle_default
    def __init__(self, list, error=None):
        self._list = list
        self._error = error

    def validate(self, data):
        if data not in self._list:
            raise SchemaError('%s not in %s' % (data, self._list), self._error)
        return data


class IntVal(object):
    """
    schema to Validate integer
    @data should be either integer or numeric string like "123"
    @data should be in @values set OR in range of @min and @max
    """
    def __init__(self, min=None, max=None, values=None, error=None):
        self._min = min
        self._max = max
        self._values = values
        self._error = error

    def validate(self, data):
        if not isinstance(data, int):
            try:
                if isinstance(data, STRING):
                    data = int(data, 0)
                else:
                    data = int(data)
            except Exception:
                raise SchemaError('%s is not integer' % data, self._error)
        if self._values:
            if data in self._values:
                return data
        if self._min is not None:
            if data < self._min:
                raise SchemaError('%d is smaller than %d' % (data, self._min), self._error)
        if self._max is not None:
            if data > self._max:
                raise SchemaError('%d is larger than %d' % (data, self._max), self._error)
        if self._min is None and self._max is None and self._values:
            raise SchemaError('%s is not in %s' % (data, self._values))
        return data


class FloatVal(object):
    """
    schema to Validate integer
    @data should be either integer or numeric string like "123"
    @data should be in @values set OR in range of @min and @max
    """
    def __init__(self, min=None, max=None, error=None):
        self._min = min
        self._max = max
        self._error = error

    def validate(self, data):
        if not isinstance(data, float):
            try:
                data = float(data)
            except Exception:
                raise SchemaError('%s is not float' % data, self._error)

        if self._min is not None:
            if data < self._min:
                raise SchemaError('%f is smaller than %f' % (data, self._min), self._error)
        if self._max is not None:
            if data > self._max:
                raise SchemaError('%f is larger than %f' % (data, self._max), self._error)

        return data


class BoolVal(object):
    """
    schema to Validate bool
    if it's not of bool type, convert it from string to bool
    under the rule: if it's "false"/"False", convert to False,
    if it's "true"/"True", convert to True.
    Otherwise, raise a SchemaError
    """
    def __init__(self, error=None):
        self._error = error

    def validate(self, data):
        if isinstance(data, bool):
            return data

        if isinstance(data, int):
            if data == 0:
                return False
            else:
                return True

        if data.lower() == 'true':
            return True
        elif data.lower() == 'false':
            return False
        else:
            raise SchemaError('fail to convert %s to bool' % data, self._error)


class StrVal(object):
    def __init__(self, min_len=None, max_len=None, invalid_char_set=None, error=None):
        self._error = error
        self._min_len = min_len
        self._max_len = max_len
        self._invalid_char_set = invalid_char_set

    def validate(self, data):
        if type(data) != str and type(data) != STRING:
            raise SchemaError('{0} in not valid string'.format(data), self._error)
        data = STRING(data).strip()
        if self._min_len is not None and len(data) < self._min_len:
            raise SchemaError('{0} should be longer than {1} characters'.format(data, self._min_len), self._error)
        if self._max_len is not None and len(data) > self._max_len:
            raise SchemaError('{0} should be shorter than {1} characters'.format(data, self._max_len), self._error)
        if self._invalid_char_set:
            for c in self._invalid_char_set:
                if c in data:
                    raise SchemaError('{0} contains invalid character {1}'.format(data, c), self._error)
        return data


class StrRe(object):
    """
    schema to Validate string against to regular express, return str type
    @data should be string which can match the given regular express
    """
    def __init__(self, pattern="", error=None):
        self._error = error
        self.pattern = re.compile(pattern)
        self.pattern_str = pattern

    def validate(self, data):
        if not isinstance(data, STRING):
            try:
                data = STRING(data)
            except Exception:
                raise SchemaError('%s is not valid string' % data, self._error)

        if self.pattern.match(data) is None:
            raise SchemaError('%s does not match regular express(%s)' % (data, self.pattern_str), self._error)
        else:
            return data


class Datetime(object):
    def __init__(self, error=None):
        self._error = error

    def validate(self, data):
        if isinstance(data, datetime.datetime):
            return data
        elif isinstance(data, STRING):
            return datetime.datetime.strptime(data, '%Y-%m-%dT%H:%M:%S')
        else:
            raise SchemaError('%s is not in datetime format' % data, self._error)


class Date(object):
    def __init__(self, error=None):
        self._error = error

    def validate(self, data):
        if isinstance(data, datetime.date):
            return data
        elif isinstance(data, STRING):
            return datetime.datetime.strptime(data, '%Y-%m-%d').date()
        else:
            raise SchemaError('%s is not in date format' % data, self._error)


class Time(object):
    def __init__(self, error=None):
        self._error = error

    def validate(self, data):
        if isinstance(data, datetime.time):
            return data
        elif isinstance(data, STRING):
            return datetime.datetime.strptime(data, '%H:%M:%S').time()
        else:
            raise SchemaError('%s is not in date format' % data, self._error)


class URIVal(object):
    # URI schema which will validate and quote given URL

    def __init__(self, scheme=None, max_len=None, error=None):
        self._scheme = scheme
        self._max_len = max_len
        self._error = error

    def validate(self, uri):
        if type(uri) != str and type(uri) != STRING:
            raise SchemaError('%s not valide string type' %uri, self._error)
        uri = uri.strip()
        if self._scheme:
            if not uri.lower().startswith(self._scheme):
                raise SchemaError('%s is not of scheme %s' % (uri, self._scheme), self._error)

        if self._max_len is not None and len(uri) > self._max_len:
            raise SchemaError('uri {0} too long'.format(uri))

        return uri


class Use(object):

    @handle_default
    def __init__(self, callable_, error=None):
        assert callable(callable_)
        self._callable = callable_
        self._error = error

    def __repr__(self):
        return '%s(%r)' % (self.__class__.__name__, self._callable)

    def validate(self, data):
        try:

            if self._callable == int and \
               isinstance(data, STRING) :
                return self._callable(data ,0)
            if self._callable == STRING:
                return self._callable(data).strip()

            return self._callable(data)
        except SchemaError as x:
            raise SchemaError([None] + x.autos, [self._error] + x.errors)
        except BaseException as x:
            f = self._callable.__name__
            raise SchemaError('%s(%r) raised %r' % (f, data, x), self._error)


class ListVal(object):
    """
    schema to Validate list which can convert a string/unicode to the list by a sep char
    """
    def __init__(self, element_type, sep=",", error=None):
        self._error = error
        self._schema = Schema([element_type])
        self.sep = sep

    def validate(self, data):
        if data is None:
            data = []
        if not isinstance(data, list):
            try:
                if data == "":
                    data = []
                else:
                    data = data.split(self.sep)
            except Exception:
                raise SchemaError('%s is not valid string or list' % data, self._error)

        return self._schema.validate(data)


class DictVal(object):
    def __init__(self, key_schema, value_schema, error=None):
        self._key_schema = key_schema
        self._value_schema = value_schema
        self._error = error

    def validate(self, data):
        if not isinstance(data, dict):
            raise SchemaError('%s is not valid dict' %data, self._error)
        d = {}
        for k, v in data.items():
            d[self._key_schema.validate(k)] = self._value_schema.validate(v)
        return d


def priority(s):
    if isinstance(s, DoNotCare):
        return 9
    if isinstance(s, AutoDel):
        return 8
    if isinstance(s, Optional):
        return 7
    if type(s) in (list, tuple, set, frozenset):
        return 6
    if type(s) is dict:
        return 5
    if hasattr(s, 'validate'):
        return 4
    if type(s) is type:
        return 3
    if callable(s):
        return 2
    else:
        return 1


class Schema(object):

    @handle_default
    def __init__(self, schema, error=None):
        self._schema = schema
        self._error = error

    def __repr__(self):
        return '%s(%r)' % (self.__class__.__name__, self._schema)

    def validate(self, data):
        s = self._schema
        e = self._error
        if type(s) in (list, tuple, set, frozenset):
            data = Schema(type(s), error=e).validate(data)
            return type(s)(Or(*s, error=e).validate(d) for d in data)
        if type(s) is dict:
            data = Schema(dict, error=e).validate(data)
            new = type(data)()
            x = None
            coverage = set() # non-optional schema keys that were matched
            for key, value in data.items():
                valid = False
                skey = None
                must_match = False
                for skey in sorted(s, key=priority):
                    svalue = s[skey]
                    try:
                        nkey = Schema(skey, error=e).validate(key)
                        if isinstance(skey, str) or isinstance(skey, Optional) or isinstance(skey, STRING):
                            must_match = True
                        try:
                            nvalue = Schema(svalue, error=e).validate(value)
                        except SchemaError as _x:
                            x = _x
                            raise
                    except SchemaError:
                        if must_match:
                            raise
                        pass
                    else:
                        coverage.add(skey)
                        valid = True
                        break
                if valid:
                    if not isinstance(skey, AutoDel):
                        new[nkey] = nvalue
                elif skey is not None:
                    if x is not None:
                        raise SchemaError(['key %r is required' % key] +
                                          x.autos, [e] + x.errors)
                    else:
                        raise SchemaError('invalid key %r' % key, e)
            required = set(k for k in s if not isinstance(k, Optional))
            # missed keys
            if not required.issubset(coverage):
                raise SchemaError('missed keys %r' % (required - coverage), e)
                # wrong keys
            #            if len(new) != len(data):
            #                raise SchemaError('wrong keys %r in %r' % (new, data), e)
            # default for optional keys
            for k in set(s) - required - coverage:
                try:
                    new[k.default] = s[k].default
                except AttributeError:
                    pass
            return new
        if hasattr(s, 'validate'):
            try:
                return s.validate(data)
            except SchemaError as x:
                raise SchemaError([None] + x.autos, [e] + x.errors)
            except BaseException as x:
                raise SchemaError('%r.validate(%r) raised %r' % (s, data, x),
                                  self._error)
        if type(s) is type:
            if isinstance(data, s):
                return data
            else:
                raise SchemaError('%r should be instance of %r' % (data, s), e)
        if callable(s):
            f = s.__name__
            try:
                if s(data):
                    return data
            except SchemaError as x:
                raise SchemaError([None] + x.autos, [e] + x.errors)
            except BaseException as x:
                raise SchemaError('%s(%r) raised %r' % (f, data, x),
                                  self._error)
            raise SchemaError('%s(%r) should evaluate to True' % (f, data), e)
        if s == data:
            return s # Jamken: Make sure the return data is absolute the specific value in case of fix value schema
        else:
            raise SchemaError('%r does not match %r' % (s, data), e)


class Optional(Schema):

    """Marker for an optional part of Schema."""

    auto_default = True


class DoNotCare(Optional):

    auto_default = True


class AutoDel(Optional):

    auto_default = True


class Default(Schema):

    """Wrapper automatically adding a default value if possible"""

    auto_default = True


if __name__ == '__main__':
    # example
    schema = Schema({"key1": STRING,       # key1 should be string
                     "key2": STRING,       # key2 should be int
                     "key3": Use(int),  # key3 should be in or int in string
                     "key4": IntVal(1, 99),   # key4 should be int between 1-99
                     Optional("key5"): Default(STRING, default="value5"),  # key5 is optional,
                                                                        # should be str and default value is "value 5"
                     DoNotCare(STRING): object})      # for keys we don't care

    from pprint import pprint

    # should pass the validation
    pprint(schema.validate({"key1": "value1",
                            "key2": 222,
                            "key3": "333",
                            "key4": 44,
                            "key_none": None,
                            "key_none2": "null"}))

    # all following cases should fail
    # import traceback

    try:
        schema.validate({"key1": "value1"})   # missing key
    except Exception as e:
        # print traceback.format_exc()
        print(e)

    try:
        schema.validate({"key1": "value1",
                         "key2": 222,
                         "key3": 333,
                         "key4": 444})   # number too large
    except Exception as e:
        print(e)

    try:
        schema.validate({"key1": "value1",
                         "key2": 222,
                         "key3": 333,
                         "key4": 0})   # not int
    except Exception as e:
        print(e)

    try:
        schema.validate({"key1": "value1",
                         "key2": 222,
                         "key3": 333,
                         "key4": 44,
                         "key5": 555})   # wrong type
    except Exception as e:
        print(e)
