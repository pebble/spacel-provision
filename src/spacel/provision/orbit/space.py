from botocore.exceptions import ClientError
import logging
from spacel.provision.cloudformation import (BaseCloudFormationFactory,
                                             key_sorted)

logger = logging.getLogger('spacel')


class SpaceElevatorOrbitFactory(BaseCloudFormationFactory):
    """
    Builds orbital VPCs based on Space Elevator templates.
    """

    def __init__(self, clients, templates):
        super(SpaceElevatorOrbitFactory, self).__init__(clients)
        self._templates = templates

    def get_orbit(self, orbit):
        self._azs(orbit)
        self._orbit_stack(orbit, 'vpc')
        self._orbit_stack(orbit, 'tables')
        self._orbit_stack(orbit, 'bastion')

        for region in orbit.regions:
            bastion_eips = sorted(orbit.bastion_eips(region).values())
            logger.debug('Bastions: %s - %s', region, ' '.join(bastion_eips))

    def _orbit_stack(self, orbit, stack_suffix):
        stack_name = '%s-%s' % (orbit.name, stack_suffix)

        updates = {}
        for region in orbit.regions:
            logger.debug('Provisioning %s in %s.', stack_name, region)
            if stack_suffix == 'vpc':
                template = self._templates.vpc(orbit, region)
            elif stack_suffix == 'bastion':
                template = self._templates.bastion(orbit, region)
            elif stack_suffix == 'tables':
                template = self._templates.tables(orbit)
            else:
                logger.warn('Unknown orbit template: %s', stack_suffix)
                return

            updated = self._stack(stack_name, region, template)
            if updated:
                updates[region] = updated

        if updates:
            logger.debug('Requested %s in %s, waiting for provisioning...',
                         stack_name, region)
            self._wait_for_updates(stack_name, updates)
        logger.debug('Provisioned %s in %s.', stack_name, region)

        # Refresh model from CF:
        for region in orbit.regions:
            cf = self._clients.cloudformation(region)
            cf_stack = self._describe_stack(cf, stack_name)
            cf_outputs = cf_stack.get('Outputs', {})
            if stack_suffix == 'tables':
                continue
            elif stack_suffix == 'vpc':
                self._orbit_from_vpc(orbit, region, cf_outputs)
            elif stack_suffix == 'bastion':
                self._orbit_from_bastion(orbit, region, cf_outputs)
            else:  # pragma: no cover
                logger.warn('Unknown suffix: %s', stack_suffix)

    def _azs(self, orbit):
        for region in orbit.regions:
            ec2 = self._clients.ec2(region)
            try:
                vpcs = ec2.describe_vpcs()
                vpc_id = vpcs['Vpcs'][0]['VpcId']
                invalid_az = region + '-zzz'
                ec2.create_subnet(VpcId=vpc_id, CidrBlock='172.31.192.0/20',
                                  AvailabilityZone=invalid_az)
            except ClientError as e:
                message = e.response['Error'].get('Message')
                if not message or 'Subnets can currently only be created in ' \
                                  'the following availability zones' not in message:
                    raise e
                message_split = message.split(region)
                # Invalid region echoed, every mention after that is an AZ:
                azs = sorted([region + s[0] for s in message_split[2:]])
                orbit._azs[region] = azs

    @staticmethod
    def _orbit_from_vpc(orbit, region, cf_outputs):
        logger.debug('Updating %s from VPC CloudFormation.', orbit.name)
        pub_instance = {}
        pub_elb = {}
        priv_instance = {}
        priv_elb = {}
        for output in cf_outputs:
            key = output['OutputKey']
            value = output['OutputValue']
            if key.startswith('PrivateInstanceSubnet'):
                priv_instance[key[-2:]] = value
            elif key.startswith('PrivateElbSubnet'):
                priv_elb[key[-2:]] = value
            elif key.startswith('PublicInstanceSubnet'):
                pub_instance[key[-2:]] = value
            elif key.startswith('PublicElbSubnet'):
                pub_elb[key[-2:]] = value
            elif key.startswith('PublicNatSubnet'):
                pass
            elif key.startswith('NatEip'):
                orbit._nat_eips[region][key[-2:]] = value
            elif key.startswith('VpcId'):
                orbit._vpc_ids[region] = value
            else:
                logger.warn('Unrecognized output key: %s', key)

        orbit._public_instance_subnets[region] = key_sorted(pub_instance)
        orbit._public_elb_subnets[region] = key_sorted(pub_elb)
        orbit._private_instance_subnets[region] = key_sorted(priv_instance)
        orbit._private_elb_subnets[region] = key_sorted(priv_elb)

    @staticmethod
    def _orbit_from_bastion(orbit, region, cf_outputs):
        logger.debug('Updating %s from Bastion CloudFormation.', orbit.name)
        for output in cf_outputs:
            key = output['OutputKey']
            value = output['OutputValue']
            if key.startswith('ElasticIp'):
                orbit._bastion_eips[region][key[-2:]] = value
            elif key.startswith('BastionSecurityGroup'):
                orbit._bastion_sgs[region] = value
            else:
                logger.warn('Unrecognized output key: %s', key)