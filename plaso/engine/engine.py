# -*- coding: utf-8 -*-
"""The processing engine."""

from __future__ import unicode_literals

import logging

from dfvfs.helpers import file_system_searcher
from dfvfs.lib import errors as dfvfs_errors
from dfvfs.path import factory as path_spec_factory
from dfvfs.resolver import resolver as path_spec_resolver

from plaso.containers import sessions
from plaso.engine import knowledge_base
from plaso.engine import processing_status
from plaso.engine import profiler
from plaso.lib import definitions
from plaso.preprocessors import manager as preprocess_manager


class BaseEngine(object):
  """Processing engine interface.

  Attributes:
    knowledge_base (KnowledgeBase): knowledge base.
  """

  # The interval of status updates in number of seconds.
  _STATUS_UPDATE_INTERVAL = 0.5

  def __init__(self):
    """Initializes an engine."""
    super(BaseEngine, self).__init__()
    self._abort = False
    self._processing_status = processing_status.ProcessingStatus()

    self.knowledge_base = knowledge_base.KnowledgeBase()

  def _DetermineOperatingSystem(self, searcher):
    """Tries to determine the underlying operating system.

    Args:
      searcher (dfvfs.FileSystemSearcher): file system searcher.

    Returns:
      str: operating system for example "Windows". This should be one of
          the values in definitions.OPERATING_SYSTEMS.
    """
    find_specs = [
        file_system_searcher.FindSpec(
            location='/etc', case_sensitive=False),
        file_system_searcher.FindSpec(
            location='/System/Library', case_sensitive=False),
        file_system_searcher.FindSpec(
            location='/Windows/System32', case_sensitive=False),
        file_system_searcher.FindSpec(
            location='/WINNT/System32', case_sensitive=False),
        file_system_searcher.FindSpec(
            location='/WINNT35/System32', case_sensitive=False),
        file_system_searcher.FindSpec(
            location='/WTSRV/System32', case_sensitive=False)]

    locations = []
    for path_spec in searcher.Find(find_specs=find_specs):
      relative_path = searcher.GetRelativePath(path_spec)
      if relative_path:
        locations.append(relative_path.lower())

    # We need to check for both forward and backward slashes since the path
    # spec will be OS dependent, as in running the tool on Windows will return
    # Windows paths (backward slash) vs. forward slash on *NIX systems.
    windows_locations = set([
        '/windows/system32', '\\windows\\system32', '/winnt/system32',
        '\\winnt\\system32', '/winnt35/system32', '\\winnt35\\system32',
        '\\wtsrv\\system32', '/wtsrv/system32'])

    if windows_locations.intersection(set(locations)):
      return definitions.OPERATING_SYSTEM_WINDOWS

    if '/system/library' in locations:
      return definitions.OPERATING_SYSTEM_MACOS

    if '/etc' in locations:
      return definitions.OPERATING_SYSTEM_LINUX

    return definitions.OPERATING_SYSTEM_UNKNOWN

  @classmethod
  def CreateSession(
      cls, command_line_arguments=None, debug_mode=False,
      filter_file=None, preferred_encoding='utf-8',
      preferred_time_zone=None, preferred_year=None):
    """Creates a session attribute container.

    Args:
      command_line_arguments (Optional[str]): the command line arguments.
      debug_mode (bool): True if debug mode was enabled.
      filter_file (Optional[str]): path to a file with find specifications.
      preferred_encoding (Optional[str]): preferred encoding.
      preferred_time_zone (Optional[str]): preferred time zone.
      preferred_year (Optional[int]): preferred year.

    Returns:
      Session: session attribute container.
    """
    session = sessions.Session()

    session.command_line_arguments = command_line_arguments
    session.debug_mode = debug_mode
    session.filter_file = filter_file
    session.preferred_encoding = preferred_encoding
    session.preferred_time_zone = preferred_time_zone
    session.preferred_year = preferred_year

    return session

  def GetSourceFileSystem(self, source_path_spec, resolver_context=None):
    """Retrieves the file system of the source.

    Args:
      source_path_spec (dfvfs.PathSpec): path specifications of the sources
          to process.
      resolver_context (dfvfs.Context): resolver context.

    Returns:
      tuple: containing:

        dfvfs.FileSystem: file system
        path.PathSpec: mount point path specification. The mount point path
            specification refers to either a directory or a volume on a storage
            media device or image. It is needed by the dfVFS file system
            searcher (FileSystemSearcher) to indicate the base location of
            the file system.

    Raises:
      RuntimeError: if source file system path specification is not set.
    """
    if not source_path_spec:
      raise RuntimeError('Missing source path specification.')

    file_system = path_spec_resolver.Resolver.OpenFileSystem(
        source_path_spec, resolver_context=resolver_context)

    type_indicator = source_path_spec.type_indicator
    if path_spec_factory.Factory.IsSystemLevelTypeIndicator(type_indicator):
      mount_point = source_path_spec
    else:
      mount_point = source_path_spec.parent

    return file_system, mount_point

  def PreprocessSources(
      self, artifacts_registry, source_path_specs, resolver_context=None):
    """Preprocesses the sources.

    Args:
      artifacts_registry (artifacts.ArtifactDefinitionsRegistry]): artifact
          definitions registry.
      source_path_specs (list[dfvfs.PathSpec]): path specifications of
          the sources to process.
      resolver_context (Optional[dfvfs.Context]): resolver context.
    """
    detected_operating_systems = []
    for source_path_spec in source_path_specs:
      try:
        file_system, mount_point = self.GetSourceFileSystem(
            source_path_spec, resolver_context=resolver_context)
      except (RuntimeError, dfvfs_errors.BackEndError) as exception:
        logging.error(exception)
        continue

      try:
        searcher = file_system_searcher.FileSystemSearcher(
            file_system, mount_point)

        operating_system = self._DetermineOperatingSystem(searcher)
        if operating_system != definitions.OPERATING_SYSTEM_UNKNOWN:
          preprocess_manager.PreprocessPluginsManager.RunPlugins(
              artifacts_registry, file_system, mount_point, self.knowledge_base)

          detected_operating_systems.append(operating_system)

      finally:
        file_system.Close()

    if detected_operating_systems:
      logging.info('Preprocessing detected operating systems: {0:s}'.format(
          ', '.join(detected_operating_systems)))
      self.knowledge_base.SetValue(
          'operating_system', detected_operating_systems[0])

  @classmethod
  def SupportsGuppyMemoryProfiling(cls):
    """Determines if memory profiling with guppy is supported.

    Returns:
      bool: True if memory profiling with guppy is supported.
    """
    return profiler.GuppyMemoryProfiler.IsSupported()
