#!/usr/bin/env python
# -*- coding: utf-8 -*-

from jinja2 import Environment, FileSystemLoader
from jinja2 import filters, StrictUndefined

import tmGrammar
import tmEventSetup

import argparse
import datetime
import shutil
import re
import sys, os

CustomFilters = {
    'hex': lambda value: format(value, '04x')
}

class Range(object):
    def __init__(self, minimum, maximum):
        self.minimum = minimum
        self.maximum = maximum
    def __str__(self):
        return "{{0x{minimum:04x}, 0x{maximum:04x}}}".format(**vars(self))

class Array(list):
    def __str__(self):
        values = [format(value) for value in self]
        return "{{{}}}".format(", ".join(values))

class ObjectHelper(object):
    Types = {
        tmEventSetup.Egamma: 'eg',
        tmEventSetup.Jet: 'jet',
    }
    def __init__(self, handle):
        self.type = self.Types[handle.getType()]
        self.threshold = 0
        self.slice = Range(0, 12)
        self.eta = Array()
        self.phi = Array()
        for cut in handle.getCuts():
            type_ = cut.getCutType()
            if type_ == tmEventSetup.Threshold:
                self.threshold = cut.getMinimum().index
            elif type_ == tmEventSetup.Slice:
                self.slice = Range(cut.getMinimum().index, cut.getMaximum().index)
            elif type_ == tmEventSetup.Eta:
                self.eta.append(Range(cut.getMinimum().index, cut.getMaximum().index))
            elif type_ == tmEventSetup.Phi:
                self.phi.append(Range(cut.getMinimum().index, cut.getMaximum().index))


class ConditionHelper(object):
    Types = {
        tmEventSetup.SingleEgamma: 'comb_cond',
        tmEventSetup.DoubleEgamma: 'comb_cond',
        tmEventSetup.TripleEgamma: 'comb_cond',
        tmEventSetup.QuadEgamma: 'comb_cond',
        tmEventSetup.SingleJet: 'comb_cond',
        tmEventSetup.DoubleJet: 'comb_cond',
        tmEventSetup.TripleJet: 'comb_cond',
        tmEventSetup.QuadJet: 'comb_cond',
    }
    def __init__(self, handle):
        self.name = handle.getName()
        self.type = self.Types[handle.getType()]
        self.objects = []
        for object_ in handle.getObjects():
            self.objects.append(ObjectHelper(object_))

class SeedHelper(object):
    Operators = {
        'NOT': 'not',
        'AND': 'and',
        'OR': 'or',
        'XOR': 'xor',
    }
    condition_namespace = 'cl'
    def __init__(self, handle):
        self.index = handle.getIndex()
        self.name = handle.getName()
        self.expression = self.__format_expr(handle.getExpressionInCondition())
    def __format_expr(self, expr):
        # replace operators
        for k,v in self.Operators.iteritems():
            expr = re.sub(r'([\)\(\s])({})([\(\s])'.format(k), r'\1{}\3'.format(v), expr)
        # replace condition names
        expr = re.sub(r'([\w_]+_i\d+)', r'{}.\1'.format(self.condition_namespace), expr)
        return expr

class TemplateEngine(object):
    """Custom tempalte engine class."""

    def __init__(self, searchpath, encoding='utf-8'):
        # Create Jinja environment.
        loader = FileSystemLoader(searchpath, encoding)
        self.environment = Environment(loader=loader, undefined=StrictUndefined)
        self.environment.filters.update(CustomFilters)

    def render(self, template, data={}):
        template = self.environment.get_template(template)
        return template.render(data)

class Distribution(object):

    def __init__(self, templates_dir, filename):
        self.templates_dir = templates_dir
        hls_dir = os.path.join(templates_dir, 'hls')
        vhdl_dir = os.path.join(templates_dir, 'vhdl')
        self.engine = TemplateEngine([hls_dir, vhdl_dir])
        self.proc = sys.argv[0]
        self.timestamp = datetime.datetime.now().isoformat().rsplit('.', 1)[0]
        self.menu = tmEventSetup.getTriggerMenu(filename)
        self.conditions = []
        for handle in self.menu.getConditionMapPtr().values():
            self.conditions.append(ConditionHelper(handle))
        self.seeds = []
        for handle in self.menu.getAlgorithmMapPtr().values():
            self.seeds.append(SeedHelper(handle))
        # sort by index
        self.seeds.sort(key=lambda seed: seed.index)


    def dist_dir(self, path, *args):
        filename = os.path.join(*args) if len(args) else ''
        return os.path.join(path, 'hls', 'module_0', 'src', 'impl', filename)

    def create_dirs(self, path, force):
        if os.path.isdir(path):
            if force:
                shutil.rmtree(path)
            else:
                raise RuntimeError("Distribution directory already exists: {}".format(path))
        if not os.path.isdir(path):
            os.makedirs(self.dist_dir(path))

    def write_template(self, template, filename, data):
        with open(filename, 'w') as fp:
            data['header_timestamp'] = self.timestamp
            data['header_menu_name'] = self.menu.getName()
            data['header_proc'] = self.proc
            data['menu'] = self.menu,
            content = self.engine.render(template, data)
            fp.write(content)

    def write_cuts(self, path):
        template = 'cuts.hxx'
        filename = self.dist_dir(path, template)
        data = {
            'conditions': self.conditions
        }
        self.write_template(template, filename, data)

    def write_conditions(self, path):
        template = 'conditions.hxx'
        filename = self.dist_dir(path, template)
        data = {
            'conditions': self.conditions
        }
        self.write_template(template, filename, data)

    def write_logic(self, path):
        template = 'seeds.hxx'
        filename = self.dist_dir(path, template)
        data = {
            'seeds': self.seeds,
            'condition_namespace': SeedHelper.condition_namespace
        }
        self.write_template(template, filename, data)

    def write_symlink(self, path, name):
        root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        cwd = os.getcwd()
        os.chdir(root_dir)
        if os.path.exists(name):
            os.unlink(name)
        os.symlink(path, name)
        os.chdir(cwd)

    def write_impl(self, path, force=False):
        path = os.path.join(path, self.menu.getName())
        self.create_dirs(path, force)
        self.write_cuts(path)
        self.write_conditions(path)
        self.write_logic(path)
        self.write_symlink(path, 'current_dist')


def parse_args():
    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    default_templates_dir = os.path.join(root_dir, 'templates')
    default_output_dir = os.path.join(root_dir, 'dist')
    parser = argparse.ArgumentParser()
    parser.add_argument('filename', help="XML trigger menu")
    parser.add_argument('--templates', metavar='<dir>', default=default_templates_dir, help="template input directory")
    parser.add_argument('--output', metavar='<dir>', default=default_output_dir, help="distribution output directory")
    parser.add_argument('--force', action='store_true', help="force overwrite an exisiting distribution")
    return parser.parse_args()

def main():
    args = parse_args()

    dist = Distribution(args.templates, args.filename)
    dist.write_impl(args.output, args.force)

if __name__ == '__main__':
    main()
