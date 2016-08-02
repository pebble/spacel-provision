from argparse import ArgumentParser
import boto3
import json
import logging
from splitstream import splitfile
import sys
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import urlopen

logger = logging.getLogger('spacel')


class ErrorEatingArgumentParser(ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)


parser = ErrorEatingArgumentParser(prog='spacel.main',
                                   description='Spacel provisioner')
parser.add_argument('orbit_url')
parser.add_argument('app_url')


def parse_args(args, in_stream):
    parsed = parser.parse_args(args)
    if not parsed.orbit_url or not parsed.app_url:
        return None, None

    if in_stream.isatty():
        in_split = iter(())
    else:
        in_split = splitfile(in_stream, format='json')

    orbit = read_manifest(parsed.orbit_url, 'orbit_url', in_split)
    app = read_manifest(parsed.app_url, 'app_url', in_split)
    return orbit, app


def parse_s3(s3_url):
    key = s3_url.path[1:]

    hostname = s3_url.hostname
    aws_pos = hostname.find('.amazonaws.com')
    if aws_pos != -1:
        host_prefix = hostname[:aws_pos]
        if '.' in host_prefix:
            bucket, host_prefix = host_prefix.split('.', 1)
        else:
            _, bucket, key = s3_url.path.split('/', 2)
        region = host_prefix.replace('s3.', '').replace('s3-', '')
    else:
        region = 'us-east-1'
        bucket = hostname

    return region, bucket, key


def read_manifest(name, label, in_split):
    url = urlparse(name)
    if url.scheme in ('http', 'https'):
        try:
            opened = urlopen(name)
            json_body = opened.read()
        except HTTPError as e:
            logger.warn('Unable to read manifest from %s: %s - %s', name,
                        e.code,
                        e.msg)
            return None
    elif url.scheme == 's3':
        region, bucket, key = parse_s3(url)
        s3 = boto3.resource('s3', region)
        json_body = s3.Object(bucket, key).get()['Body'].read()
    elif not url.scheme and url.path == '-':
        try:
            json_body = in_split.__next__()
        except StopIteration:
            logger.warn('Unable to read %s manifest from stdin.', label)
            return None
    else:
        logger.warn('Invalid input URL for %s: %s', label, name)
        return None

    if json_body:
        return json.loads(json_body.decode('utf-8'))