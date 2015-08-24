import logging; _L = logging.getLogger('openaddr.ci.collect')

from ..compat import standard_library

from argparse import ArgumentParser
from os import close, remove, utime, environ
from zipfile import ZipFile, ZIP_DEFLATED
from os.path import relpath, splitext, exists
from urllib.parse import urlparse
from operator import attrgetter
from tempfile import mkstemp
from calendar import timegm

from dateutil.parser import parse
from requests import get

from .objects import read_latest_set, read_completed_runs_to_date
from . import db_connect, db_cursor, setup_logger
from .. import S3

parser = ArgumentParser(description='Run some source files.')

parser.add_argument('-o', '--owner', default='openaddresses',
                    help='Github repository owner. Defaults to "openaddresses".')

parser.add_argument('-r', '--repository', default='openaddresses',
                    help='Github repository name. Defaults to "openaddresses".')

parser.add_argument('-d', '--database-url', default=environ.get('DATABASE_URL', None),
                    help='Optional connection string for database. Defaults to value of DATABASE_URL environment variable.')

def main():
    ''' Single threaded worker to serve the job queue.
    '''
    args = parser.parse_args()
    setup_logger(environ.get('AWS_SNS_ARN'))

    # Rely on boto AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY variables.
    s3 = S3(None, None, environ.get('AWS_S3_BUCKET', 'data.openaddresses.io'))
    
    with db_connect(args.database_url) as conn:
        with db_cursor(conn) as db:
            set = read_latest_set(db, args.owner, args.repository)
            runs = read_completed_runs_to_date(db, set.id)
    
    try:
        handle, filename = mkstemp(prefix='collected-', suffix='.zip')
        close(handle)

        collected_zip = ZipFile(filename, 'w', ZIP_DEFLATED, allowZip64=True)
        collector = collect_and_publish(s3, collected_zip, 'openaddresses-collected.zip')

        for file in iterate_local_processed_files(runs):
            collector.send(file)
        
        collector.close()
        
    finally:
        remove(collected_zip.filename)
    
def collect_and_publish(s3, collection_zip, public_name):
    ''' Returns a primed generator-iterator to accept sent source/filename tuples.
    
        Each is added to the passed ZipFile. On completion, a new S3 object
        is created with public_name and the collection is closed and uploaded.
    '''
    def get_collector_publisher():
        while True:
            try:
                (source_base, filename) = yield
            except GeneratorExit:
                break
            else:
                _L.info(u'Adding {} to {}'.format(source_base, collection_zip.filename))
                add_source_to_zipfile(collection_zip, source_base, filename)

        collection_zip.close()
        _L.info(u'Finished {}'.format(collection_zip.filename))

        zip_key = s3.new_key(public_name)
        zip_args = dict(policy='public-read', headers={'Content-Type': 'application/zip'})
        zip_key.set_contents_from_filename(collection_zip.filename, **zip_args)
        _L.info(u'Uploaded {} to {}'.format(collection_zip.filename, public_name))
  
    collector_publisher = get_collector_publisher()

    # Generator-iterator must be primed:
    # https://docs.python.org/2.7/reference/expressions.html#generator.next
    collector_publisher.next()

    return collector_publisher

def add_source_to_zipfile(zip_out, source_base, filename):
    '''
    '''
    _, ext = splitext(filename)

    if ext == '.csv':
        zip_out.write(filename, source_base + ext)
    
    elif ext == '.zip':
        zip_in = ZipFile(filename, 'r')
        for zipinfo in zip_in.infolist():
            zip_out.writestr(zipinfo, zip_in.read(zipinfo.filename))
        zip_in.close()

def iterate_local_processed_files(runs):
    ''' Yield a stream of local processed result files for a list of runs.
    '''
    for run in sorted(runs, key=attrgetter('source_path')):
        source_base, _ = splitext(relpath(run.source_path, 'sources'))
        processed_url = run.state and run.state.get('processed')
    
        if not processed_url:
            continue
        
        try:
            filename = download_processed_file(processed_url)
            yield (source_base, filename)
        
        except:
            _L.error('Failed to download {}'.format(processed_url))
        
        finally:
            if exists(filename):
                remove(filename)

def _is_us_state(abbr, source_base, filename):
    for sep in ('/', '-'):
        if source_base == 'us{sep}{abbr}'.format(**locals()):
            return True

        if source_base.startswith('us{sep}{abbr}.'.format(**locals())):
            return True

        if source_base.startswith('us{sep}{abbr}{sep}'.format(**locals())):
            return True

    return False

def is_us_northeast((source_base, filename)):
    for abbr in ('ct', 'me', 'ma', 'nh', 'ri', 'vt', 'nj', 'ny', 'pa'):
        if _is_us_state(abbr, source_base, filename):
            return True

    return False
    
def is_us_midwest((source_base, filename)):
    for abbr in ('il', 'in', 'mi', 'oh', 'wi', 'ia', 'ks', 'mn', 'mo', 'ne', 'nd', 'sd'):
        if _is_us_state(abbr, source_base, filename):
            return True

    return False
    
def is_us_south((source_base, filename)):
    for abbr in ('de', 'fl', 'ga', 'md', 'nc', 'sc', 'va', 'dc', 'wv', 'al',
                 'ky', 'ms', 'ar', 'la', 'ok', 'tx'):
        if _is_us_state(abbr, source_base, filename):
            return True

    return False
    
def is_us_west((source_base, filename)):
    for abbr in ('az', 'co', 'id', 'mt', 'nv', 'nm', 'ut', 'wy', 'ak', 'ca', 'hi', 'or', 'wa'):
        if _is_us_state(abbr, source_base, filename):
            return True

    return False
    
def _is_country(iso, source_base, filename):
    for sep in ('/', '-'):
        if source_base == iso:
            return True

        if source_base.startswith('{iso}.'.format(**locals())):
            return True

        if source_base.startswith('{iso}{sep}'.format(**locals())):
            return True

    return False

def is_europe((source_base, filename)):
    for iso in ('be', 'bg', 'cz', 'dk', 'de', 'ee', 'ie', 'el', 'es', 'fr',
                'hr', 'it', 'cy', 'lv', 'lt', 'lu', 'hu', 'mt', 'nl', 'at',
                'pl', 'pt', 'ro', 'si', 'sk', 'fi', 'se', 'uk', 'gr', 'gb'  ):
        if _is_country(iso, source_base, filename):
            return True

    return False
    
def is_asia((source_base, filename)):
    for iso in ('af', 'am', 'az', 'bh', 'bd', 'bt', 'bn', 'kh', 'cn', 'cx',
                'cc', 'io', 'ge', 'hk', 'in', 'id', 'ir', 'iq', 'il', 'jp',
                'jo', 'kz', 'kp', 'kr', 'kw', 'kg', 'la', 'lb', 'mo', 'my',
                'mv', 'mn', 'mm', 'np', 'om', 'pk', 'ph', 'qa', 'sa', 'sg',
                'lk', 'sy', 'tw', 'tj', 'th', 'tr', 'tm', 'ae', 'uz', 'vn',
                'ye', 'ps',
                
                'as', 'au', 'nz', 'ck', 'fj', 'pf', 'gu', 'ki', 'mp', 'mh',
                'fm', 'um', 'nr', 'nc', 'nz', 'nu', 'nf', 'pw', 'pg', 'mp',
                'sb', 'tk', 'to', 'tv', 'vu', 'um', 'wf', 'ws', 'is'):
        if _is_country(iso, source_base, filename):
            return True

    return False
    
def download_processed_file(url):
    ''' Download a URL to a local temporary file, return its path.
    
        Local file will have an appropriate timestamp and extension.
    '''
    _, ext = splitext(urlparse(url).path)
    handle, filename = mkstemp(prefix='processed-', suffix=ext)
    close(handle)
    
    response = get(url, stream=True)
    
    with open(filename, 'wb') as file:
        for chunk in response.iter_content(chunk_size=8192):
            file.write(chunk)
    
    last_modified = response.headers.get('Last-Modified')
    timestamp = timegm(parse(last_modified).utctimetuple())
    utime(filename, (timestamp, timestamp))
    
    return filename

if __name__ == '__main__':
    exit(main())
