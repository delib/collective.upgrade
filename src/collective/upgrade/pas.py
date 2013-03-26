import mimetypes
import tempfile
import csv

import transaction

from AccessControl import owner

from Products.PluggableAuthService.interfaces.plugins import (
    IUserEnumerationPlugin, IGroupsPlugin, IGroupEnumerationPlugin,
    IPropertiesPlugin)

from Products.CMFCore.utils import getToolByName


class Reconciler(object):

    filename = 'reconcile_{}s.csv'

    def __init__(self, context, principal_type):
        self.context = context

        principal_type = principal_type.lower()
        self.principal_type = principal_type
        self.filename = self.filename.format(principal_type)

        self.site = context.getSite()
        self.acl_users = getToolByName(self.site, 'acl_users')
        self.plugins = self.acl_users._getOb('plugins')


class ExportReconciler(Reconciler):

    fieldnames = ('Source Plugin ID',
                  'Source ID',
                  'Destination Plugin ID',
                  'Destination ID',
                  'Destination Duplicate IDs')
    user_properties = ('fullname', )

    def __init__(self, context, principal_type,
                 dest_users_plugin=None, dest_properties_plugin=None,
                 dest_groups_plugin=None):
        super(ExportReconciler, self).__init__(context, principal_type)
        self.get_rows = getattr(
            self, 'get_{}_rows'.format(self.principal_type))

        if principal_type == 'user':
            if not dest_users_plugin:
                dest_users_plugin = self.plugins.listPlugins(
                    IUserEnumerationPlugin)[0][0]
            self.dest_users = self.acl_users._getOb(dest_users_plugin)
            if not dest_properties_plugin:
                dest_properties_plugin = self.plugins.listPlugins(
                    IPropertiesPlugin)[0][0]
            self.dest_properties = self.acl_users._getOb(
                dest_properties_plugin)
        else:
            if not dest_groups_plugin:
                dest_groups_plugin = self.plugins.listPlugins(
                    IGroupEnumerationPlugin)[0][0]
            self.dest_groups = self.acl_users._getOb(dest_groups_plugin)

    def export_rows(self):
        if hasattr(self.context, 'openDataFile'):
            csvfile = self.context.openDataFile(self.filename)
        else:
            csvfile = tempfile.TemporaryFile()

        try:
            content_type = mimetypes.guess_type(self.filename)
            writer = csv.DictWriter(csvfile, self.fieldnames)
            writer.writerow(dict((name, name) for name in self.fieldnames))
            writer.writerows(self.get_rows())

            if not hasattr(self.context, 'openDataFile'):
                csvfile.seek(0)
                self.context.writeDataFile(
                    self.filename, csvfile.read(), content_type)

        finally:
            csvfile.close()

    def get_user_rows(self):
        seen = set()
        # Temporarily disable the destination plugins from listing
        savepoint = transaction.savepoint(optimistic=True)
        try:
            self.plugins.deactivatePlugin(
                IUserEnumerationPlugin, self.dest_users.getId())
            if self.dest_properties.getId() in self.plugins.listPluginIds(
                IUserEnumerationPlugin):
                self.plugins.deactivatePlugin(
                    IUserEnumerationPlugin, self.dest_properties.getId())

            # Look for matches for the source users
            for info in self.acl_users.searchUsers():
                if info['id'] in seen:
                    continue
                seen.add(info['id'])
                result = {'Source Plugin ID': info['pluginid'],
                          'Source ID': info['id']}

                # Exact user id match
                matches = self.dest_users.enumerateUsers(
                    id=info['id'], exact_match=True)
                if len(matches) == 1:
                    result['Destination Plugin ID'] = matches[0].get(
                        'pluginid', self.dest_users.getId())
                    result['Destination ID'] = matches[0]['id']
                    yield result
                    continue

                # Match on properties
                user = self.acl_users.getUserById(info['id'])
                value = None
                if user is not None:
                    for prop in self.user_properties:
                        prop_result = result.copy()
                        value = None
                        for sheet_id in user.listPropertysheets():
                            sheet = user.getPropertysheet(sheet_id)
                            if sheet.hasProperty(prop):
                                value = sheet.getProperty(prop)
                                if value:
                                    break
                        else:
                            continue
                        matches = self.dest_properties.enumerateUsers(
                            **{prop: value})
                        if matches:
                            prop_result['Destination Plugin ID'] = matches[
                                0].get('pluginid', self.dest_users.getId())
                            prop_result['Destination ID'] = matches[0]['id']
                            if len(matches) > 1:
                                prop_result[
                                    'Destination Duplicate IDs'] = ' '.join(
                                    match['id'] for match in matches[1:])
                            yield prop_result

                # No match
                if not value:
                    yield result

        # Restore disabled plugins
        finally:
            savepoint.rollback()

    def get_group_rows(self):
        seen = set()
        # Temporarily disable the destination plugins from listing
        savepoint = transaction.savepoint(optimistic=True)
        try:
            self.plugins.deactivatePlugin(
                IGroupEnumerationPlugin, self.dest_groups.getId())

            # Look for matches for the source groups
            for info in self.acl_users.searchGroups():
                if info['id'] in seen:
                    continue
                seen.add(info['id'])
                result = {'Source Plugin ID': info['pluginid'],
                          'Source ID': info['id']}

                # Exact group id match
                matches = self.dest_groups.enumerateGroups(
                    id=info['id'], exact_match=True)
                if len(matches) == 1:
                    result['Destination Plugin ID'] = matches[0].get(
                        'pluginid', self.dest_groups.getId())
                    result['Destination ID'] = matches[0]['id']
                    yield result
                    continue

                # Match on group title
                if info.get('title'):
                    matches = self.dest_groups.enumerateGroups(
                        title=info['title'])
                    if matches:
                        result['Destination Plugin ID'] = matches[0].get(
                            'pluginid', self.dest_groups.getId())
                        result['Destination ID'] = matches[0]['id']
                        if len(matches) > 1:
                            result[
                                'Destination Duplicate IDs'] = ' '.join(
                                match['id'] for match in matches[1:])
                        yield result
                        continue

                # No match
                yield result

        # Restore disabled plugins
        finally:
            savepoint.rollback()


class ImportReconciler(Reconciler):

    def import_rows(self):
        if hasattr(self.context, 'openDataFile'):
            csvfile = self.context.openDataFile(self.filename)
            if csvfile is None:
                return
        else:
            datafile = self.context.readDataFile(self.filename)
            if datafile is None:
                return
            csvfile = tempfile.TemporaryFile()
            csvfile.write(datafile)
            csvfile.seek(0)
        reader = csv.DictReader(csvfile)

        getPrincipalById = getattr(self.acl_users, 'get{}ById'.format(
            self.principal_type.capitalize()))
        rows = {}
        for row in reader:
            if not row.get('Destination ID'):
                continue
            rows[row['Source ID']] = row['Destination ID']
            source_principal = getPrincipalById(row['Source ID'])

            groupmakers = self.plugins.listPlugins(IGroupsPlugin)
            for groupmaker_id, groupmaker in groupmakers:
                if not hasattr(groupmaker, 'addPrincipalToGroup'):
                    continue
                groups = groupmaker.getGroupsForPrincipal(source_principal)
                for group in groups:
                    groupmaker.addPrincipalToGroup(
                        row['Destination ID'], group)
                    groupmaker.removePrincipalFromGroup(
                        source_principal.getId(), group)

        acl_users_path = owner.ownerInfo(self.plugins)[0]

        def import_ofs_obj(obj, path=None,
                           acl_users_path=acl_users_path, rows=rows,
                           getPrincipalById=getPrincipalById):
            userdb_path, user_id = obj.getOwnerTuple()

            creators = getattr(obj, 'listCreators', [])
            if callable(creators):
                creators = list(creators())
            contributors = getattr(obj, 'listContributors', [])
            if callable(contributors):
                contributors = list(contributors())

            for source_id, dest_id in rows.iteritems():
                # ownership
                if (acl_users_path, source_id) == (
                    userdb_path, user_id):
                    dest_principal = getPrincipalById(dest_id)
                    obj.changeOwnership(dest_principal)

                # local roles
                local_roles = obj.get_local_roles_for_userid(source_id)
                if local_roles:
                    obj.manage_addLocalRoles(dest_id, local_roles)
                    obj.manage_delLocalRoles([source_id])

                # CMF creators and contributors
                if source_id in creators:
                    creators[creators.index(source_id)] = dest_id
                if source_id in contributors:
                    contributors[contributors.index(source_id)] = dest_id

            if creators:
                obj.setCreators(creators)
            if contributors:
                obj.setContributors(contributors)

        if rows:
            self.site.ZopeFindAndApply(self.site, apply_func=import_ofs_obj)


class DataFile(object):

    def __init__(self, file_):
        self.size = file_.tell()
        file_.seek(0)
        self.file = file_


def reconcileUsersExport(context):
    reconciler = ExportReconciler(context, 'user')
    reconciler.export_rows()


def reconcileGroupsExport(context):
    reconciler = ExportReconciler(context, 'group')
    reconciler.export_rows()


def reconcileUsersImport(context):
    reconciler = ImportReconciler(context, 'user')
    reconciler.import_rows()


def reconcileGroupsImport(context):
    reconciler = ImportReconciler(context, 'group')
    reconciler.import_rows()