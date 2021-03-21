#!/usr/bin/env python3
#
# Copyright (C) 2020-2021 VyOS maintainers and contributors
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 or later as
# published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import os

from sys import exit

from vyos.config import Config
from vyos.configdict import node_changed
from vyos import ConfigError
from vyos.util import call
from vyos.util import dict_search
from vyos.template import render_to_string
from vyos import frr
from vyos import airbag
airbag.enable()

def get_config(config=None):
    if config:
        conf = config
    else:
        conf = Config()
    base = ['protocols', 'isis']
    isis = conf.get_config_dict(base, key_mangling=('-', '_'),
                                get_first_key=True)
    return isis

def verify(isis):
    # bail out early - looks like removal from running config
    if not isis:
        return None

    if 'domain' not in isis:
        raise ConfigError('Routing domain name/tag must be set!')

    if 'net' not in isis:
        raise ConfigError('Network entity is mandatory!')

    # If interface not set
    if 'interface' not in isis:
        raise ConfigError('Interface used for routing updates is mandatory!')

    # If md5 and plaintext-password set at the same time
    if 'area_password' in isis:
        if {'md5', 'plaintext_password'} <= set(isis['encryption']):
            raise ConfigError('Can not use both md5 and plaintext-password for ISIS area-password!')

    # If one param from delay set, but not set others
    if 'spf_delay_ietf' in isis:
        required_timers = ['holddown', 'init_delay', 'long_delay', 'short_delay', 'time_to_learn']
        exist_timers = []
        for elm_timer in required_timers:
            if elm_timer in isis['spf_delay_ietf']:
                exist_timers.append(elm_timer)

        exist_timers = set(required_timers).difference(set(exist_timers))
        if len(exist_timers) > 0:
            raise ConfigError('All types of delay must be specified: ' + ', '.join(exist_timers).replace('_', '-'))

    # If Redistribute set, but level don't set
    if 'redistribute' in isis:
        proc_level = isis.get('level','').replace('-','_')
        for proto, proto_config in isis.get('redistribute', {}).get('ipv4', {}).items():
            if 'level_1' not in proto_config and 'level_2' not in proto_config:
                raise ConfigError('Redistribute level-1 or level-2 should be specified in \"protocols isis {} redistribute ipv4 {}\"'.format(process, proto))
        for redistribute_level in proto_config.keys():
            if proc_level and proc_level != 'level_1_2' and proc_level != redistribute_level:
                raise ConfigError('\"protocols isis {0} redistribute ipv4 {2} {3}\" cannot be used with \"protocols isis {0} level {1}\"'.format(process, proc_level, proto, redistribute_level))

    # Segment routing checks
    if dict_search('segment_routing', isis):
        if dict_search('segment_routing.global_block', isis):
            high_label_value = dict_search('segment_routing.global_block.high_label_value', isis)
            low_label_value = dict_search('segment_routing.global_block.low_label_value', isis)
            # If segment routing global block high value is blank, throw error
            if low_label_value and not high_label_value:
                raise ConfigError('Segment routing global block high value must not be left blank')
            # If segment routing global block low value is blank, throw error
            if high_label_value and not low_label_value:
                raise ConfigError('Segment routing global block low value must not be left blank')
            # If segment routing global block low value is higher than the high value, throw error
            if int(low_label_value) > int(high_label_value):
                raise ConfigError('Segment routing global block low value must be lower than high value')

        if dict_search('segment_routing.local_block', isis):
            high_label_value = dict_search('segment_routing.local_block.high_label_value', isis)
            low_label_value = dict_search('segment_routing.local_block.low_label_value', isis)
            # If segment routing local block high value is blank, throw error
            if low_label_value and not high_label_value:
                raise ConfigError('Segment routing local block high value must not be left blank')
            # If segment routing local block low value is blank, throw error
            if high_label_value and not low_label_value:
                raise ConfigError('Segment routing local block low value must not be left blank')
            # If segment routing local block low value is higher than the high value, throw error
            if int(low_label_value) > int(high_label_value):
                raise ConfigError('Segment routing local block low value must be lower than high value')

    return None

def generate(isis):
    if not isis:
        isis['new_frr_config'] = ''
        return None

    isis['new_frr_config'] = render_to_string('frr/isis.frr.tmpl', isis)
    return None

def apply(isis):
    # Save original configuration prior to starting any commit actions
    frr_cfg = frr.FRRConfig()
    frr_cfg.load_configuration(daemon='isisd')
    frr_cfg.modify_section('^interface \S+$', '')
    frr_cfg.modify_section('^router isis \S+$', '')
    frr_cfg.add_before(r'(ip prefix-list .*|route-map .*|line vty)', isis['new_frr_config'])
    frr_cfg.commit_configuration(daemon='isisd')

    # If FRR config is blank, rerun the blank commit x times due to frr-reload
    # behavior/bug not properly clearing out on one commit.
    if isis['new_frr_config'] == '':
        for a in range(5):
            frr_cfg.commit_configuration(daemon='isisd')

    return None

if __name__ == '__main__':
    try:
        c = get_config()
        verify(c)
        generate(c)
        apply(c)
    except ConfigError as e:
        print(e)
        exit(1)
