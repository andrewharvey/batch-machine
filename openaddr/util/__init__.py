import logging; _L = logging.getLogger('openaddr.util')

from urllib.parse import urlparse, parse_qsl
from datetime import datetime, timedelta
from os.path import join, dirname

from .. import __version__
from ..compat import quote

def prepare_db_kwargs(dsn):
    '''
    '''
    p = urlparse(dsn)
    q = dict(parse_qsl(p.query))

    assert p.scheme == 'postgres'
    kwargs = dict(user=p.username, password=p.password, host=p.hostname, port=p.port)
    kwargs.update(dict(database=p.path.lstrip('/')))

    if 'sslmode' in q:
        kwargs.update(dict(sslmode=q['sslmode']))
    
    return kwargs

def set_autoscale_capacity(autoscale, cloudwatch, capacity):
    '''
    '''
    span, now = 60 * 60 * 3, datetime.now()
    start, end = now - timedelta(seconds=span), now
    args = 'tasks queue', 'openaddr.ci', 'Maximum'

    (measure, ) = cloudwatch.get_metric_statistics(span, start, end, *args)

    group_name = 'CI Workers {0}.x'.format(*__version__.split('.'))
    (group, ) = autoscale.get_all_groups([group_name])
    
    if group.desired_capacity >= capacity:
        return
    
    if measure['Maximum'] > .9:
        group.set_capacity(capacity)

def request_task_instance(ec2, autoscale, chef_role, command):
    '''
    '''
    group_name = 'CI Workers {0}.x'.format(*__version__.split('.'))

    (group, ) = autoscale.get_all_groups([group_name])
    (config, ) = autoscale.get_all_launch_configurations(names=[group.launch_config_name])
    (image, ) = ec2.get_all_images(image_ids=[config.image_id])
    keypair = ec2.get_all_key_pairs()[0]
    
    with open(join(dirname(__file__), 'templates', 'task-instance-userdata.sh')) as file:
        userdata_kwargs = dict(role=chef_role, command=' '.join(map(quote, command)))
        userdata_kwargs.update(version=quote(__version__))
    
        run_kwargs = dict(instance_type='m3.medium', security_groups=['default'],
                          instance_initiated_shutdown_behavior='terminate',
                          user_data=file.read().format(**userdata_kwargs),
                          key_name=keypair.name)

    reservation = image.run(**run_kwargs)
    (instance, ) = reservation.instances
    instance.add_tag('Name', 'Scheduled {} {}'.format(datetime.now().strftime('%Y-%m-%d'), command[0]))
    
    _L.info('Started EC2 instance {} from AMI {}'.format(instance, image))
    
    return instance
