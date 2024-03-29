import os
import logging
import jsonpickle
import yaml
from pathlib import Path
import eons
from .Exceptions import *

class Builder(eons.StandardFunctor):

	primaryFunctionName = "Build"

	def __init__(this, name=eons.INVALID_NAME()):
		super().__init__(name)

		# What can this build, "exe", "lib", "img", ... ?
		this.supportedProjectTypes = []

		this.projectType = None
		this.projectName = None
		this.clearBuildPath = False

		try:
			this.fetchFrom.remove('globals')
		except:
			pass

		this.configNameOverrides = {
			"name": "projectName",
			"type": "projectType",
		}

		this.functionSucceeded = True
		this.enableRollback = False

		this.events = []


	# Build things!
	# Override this or die.
	# Empty Builders can be used with build.json to start build trees.
	def Build(this):
		pass


	# Override this to perform whatever success checks are necessary.
	# This will be called before running the next build step.
	def DidBuildSucceed(this):
		return this.functionSucceeded


	# API compatibility shim
	def DidFunctionSucceed(this):
		return this.DidBuildSucceed()


	# Hook for any pre-build configuration
	def PreBuild(this):
		pass


	# Hook for any post-build configuration
	def PostBuild(this):
		pass


	# Sets the build path that should be used by children of *this.
	# Also sets src, inc, lib, and dep paths, if they are present.
	def PopulatePaths(this, rootPath, buildFolder):
		paths = [
			"src",
			"inc",
			"dep",
			"lib",
			"exe",
			"test"
		]

		if (rootPath is None):
			logging.warning("no \"dir\" supplied. Paths unavailable.")
			this.rootPath = None
			this.buildPath = None
			for path in paths:
				setattr(this, f"{path}Path", None)
			return

		this.rootPath = str(Path(rootPath).resolve())
		logging.debug(f"rootPath for {this.name} is {this.rootPath}")

		this.buildPath = str(Path(this.rootPath).joinpath(buildFolder))
		Path(this.buildPath).mkdir(parents=True, exist_ok=True)

		logging.debug(f"buildPath for {this.name} is {this.buildPath}")

		for path in paths:
			tmpPath = os.path.abspath(os.path.join(this.rootPath, path))
			if (os.path.isdir(tmpPath)):
				setattr(this, f"{path}Path", tmpPath)
			else:
				setattr(this, f"{path}Path", None)
			logging.debug(f"{path}Path for {this.name} is {getattr(this, f'{path}Path')}")


	# Populate the configuration details for *this.
	def PopulateLocalConfig(this, configName=None):
		localConfigFile = None
		if (not configName):
			if (this.executor):
				for ext in this.executor.default.config.extensions:
					possibleConfigName = f"build.{type(this).__name__}.{ext}"
					possibleConfig = Path(this.buildPath).joinpath(possibleConfigName)
					logging.debug(f"Looking for configuration file: {possibleConfig}")
					if (possibleConfig.exists()):
						configName = possibleConfigName
						localConfigFile = possibleConfig
						logging.debug(f"Found configuration file: {configName}")
						break
			else:
				configName = f"build.{type(this).__name__}.json"
				
		if (not localConfigFile):
			try:
				localConfigFile = Path(this.buildPath).joinpath(configName)
			except:
				localConfigFile = None
		
		if (not configName or not localConfigFile or not localConfigFile.exists()):
			if (this.executor and not this.precursor):
				this.config = this.executor.config
				logging.debug(f"Using executor config: {this.config}")
				return
			else:
				logging.info(f"Could not find a configuration file for {this.name}")
				this.config = {} # safer than n
				return
			
		configType = str(localConfigFile).split(".")[-1]
		localConfigFile = open(localConfigFile, "r")

		if (this.executor):
			this.config = this.executor.ParseConfigFile(this.executor, configType, localConfigFile)
			return
			
		if (configType in ['json', 'yml', 'yaml']):
			# Yaml doesn't allow tabs. We do. Convert.
			this.config = yaml.safe_load(localConfigFile.read().replace('\t', '  '))
			return
		
		raise OtherBuildError(f"Config file type {configType} is not supported. Consider supplying an executor to {this.name}.")


	# Calls PopulatePaths and PopulateVars after getting information from local directory
	# Projects should have a name of {project-type}_{project-name}.
	# For information on how projects should be labelled see: https://eons.llc/convention/naming/
	# For information on how projects should be organized, see: https://eons.llc/convention/uri-names/
	def PopulateProjectDetails(this):
		if ('path' in this.kwargs and 'build_in' in this.kwargs):
			this.PopulatePaths(this.kwargs.pop('path'), this.kwargs.pop('build_in'))
			this.PopulateLocalConfig()
			details = os.path.basename(this.rootPath).split(".")
		else:
			this.PopulatePaths(None, None)
			this.config = None
			details = [this.name]

		default_type = details[-1]
		default_name = default_type
		if (len(details) > 1):
			default_name = '.'.join(details[:-1])

		# This is messy because we can't query this.name or executor.name and need to get "name" from a config or arg val to set projectName.
		for key, mem in this.configNameOverrides.items():
			this.Set(mem, this.FetchWithout(['this', 'executor', 'precursor', 'globals'], key, default=this.executor.FetchWithout(['this', 'globals'], key, default=eval(f"default_{key}"), start=False)[0]))
			# if (getattr(this, mem) is None):
			# 	logging.warning(f"Not configured: {key}")

		# The clearBuildPath needs to be even more conserved than the configNameOverrides.
		# The 'clear_build_path' key is required but must come from either an argument or the config.
		this.clearBuildPath = this.Fetch('clear_build_path', False, ['args', 'config'])


	# When Fetching what to do next, we want either the executor's config or our config. Everything else will just muck things up.
	def PopulateNext(this):
		this.Set('next', this.Fetch('next', [], ['args', 'config']), evaluateExpressions=False)


	# Override of eons.Functor method. See that class for details.
	def ParseInitialArgs(this):
		super().ParseInitialArgs()
		if ('events' in this.kwargs):
			this.events = this.kwargs.pop('events')
		else:
			logging.warning(f"{this.name} found no events.")
		this.PopulateProjectDetails()


	# RETURNS whether or not we should trigger the next Builder based on what events invoked ebbs.
	# Anything in the "run_when_any" list will require a corresponding --event specification to run.
	# For example "run_when_any":["publish"] would require `--event publish` to enable publication Builders in the workflow.
	def ValidateNext(this, nextBuilder):		
		if ("run_when_none" in nextBuilder):
			if ([r for r in nextBuilder["run_when_none"] if r in this.events]):
				logging.info(f"Skipping next builder: {nextBuilder['build']}; prohibitive events found (cannot have any of {nextBuilder['run_when_none']} and have {this.events})")
				return False

		if ("run_when_any" in nextBuilder):
			if (not [r for r in nextBuilder["run_when_any"] if r in this.events]): #[] is false
				logging.info(f"Skipping next builder: {nextBuilder['build']}; required events not met (needs any of {nextBuilder['run_when_any']} but only have {this.events})")
				return False

		if ("run_when_all" in nextBuilder):
			if (not set([str(r) for r in nextBuilder["run_when_all"]]).issubset(this.events)):
				logging.info(f"Skipping next builder: {nextBuilder['build']}; required events not met (needs all {nextBuilder['run_when_all']} but only have {this.events})")
				return False

		return True


	# Creates the folder structure for the next build step.
	# RETURNS the next buildPath.
	def PrepareNext(this, nextBuilder):
		if (not this.buildPath):
			return None

		logging.debug(f"<---- Preparing for next builder: {nextBuilder['build']} ---->")

		nextBuildPath = "build"
		if ('build_in' in nextBuilder):
			nextBuildPath = nextBuilder['build_in']

		nextRootPath = "."
		if ("path" in nextBuilder):
			nextRootPath = nextBuilder["path"]
		if (nextRootPath.startswith("/")):
			nextRootPath = Path(this.executor.rootPath).joinpath(nextRootPath[1:])
		else:
			nextRootPath = Path(this.buildPath).joinpath(nextRootPath)

		nextBuildPath = nextRootPath.joinpath(nextBuildPath)

		# mkpath(nextRootPath) <- just broken.
		nextBuildPath.mkdir(parents=True, exist_ok=True)
		logging.debug(f"Next build path is: {nextBuildPath}")

		if ("copy" in nextBuilder):
			# dict() is necessary to strip off any wrappers, like DotDict, etc.
			# otherwise getattr(nextBuilder, 'copy') gives the built in copy method...
			for cpy in dict(nextBuilder)["copy"]:
				# logging.debug(f"copying: {cpy}")
				for src, dst in cpy.items():
					this.Copy(src, Path(nextRootPath).joinpath(dst), root=this.executor.rootPath)

		if ("config" in nextBuilder and nextBuilder["config"]):
			nextConfigFileName = f"build.{nextBuilder['build']}.json"
			nextConfigFile = nextBuildPath.joinpath(nextConfigFileName)
			logging.debug(f"writing: {nextConfigFile}")
			nextConfig = open(nextConfigFile, "w")
			for key, var in this.configNameOverrides.items():
				if (key not in nextBuilder["config"]):
					val = getattr(this, var)
					logging.debug(f"Adding to config: {key} = {val}")
					nextBuilder["config"][key] = val
			nextConfig.write(jsonpickle.encode(dict(nextBuilder["config"])))
			nextConfig.close()

		logging.debug(f">---- Completed preparation for: {nextBuilder['build']} ----<")
		return nextRootPath


	# Runs the next Builder.
	# Uses the Executor passed to *this.
	# RETURNS: True if all next build steps succeeded; False if any Failed.
	def CallNext(this):
		if (this.next is None):
			return None

		ret = None
		for nxt in this.next:
			if (not this.ValidateNext(nxt)):
				continue
			nxtPath = this.PrepareNext(nxt)
			buildFolder = f"then_build_{nxt['build']}"
			if ("build_in" in nxt):
				buildFolder = nxt["build_in"]
			ret = this.executor.Build(
				build=nxt["build"],
				path=nxtPath,
				build_in=buildFolder,
				events=this.events,
				precursor=this)
		return ret


	# Override of eons.UserFunctor method. See that class for details.
	def ValidateArgs(this):
		super().ValidateArgs()


	# Override of eons.Functor method. See that class for details
	def Function(this):
		logging.debug(f"<---- Preparing {this.name} ---->")

		if (this.buildPath):
			if (this.clearBuildPath):
				this.Delete(this.buildPath)

			# mkpath(this.buildPath) <- This just straight up doesn't work. Race condition???
			Path(this.buildPath).mkdir(parents=True, exist_ok=True)
			os.chdir(this.buildPath)

		this.PreBuild()

		if (len(this.supportedProjectTypes) and this.projectType not in this.supportedProjectTypes):
			raise ProjectTypeNotSupported(
				f"{this.projectType} is not supported. Supported project types for {this.name} are {this.supportedProjectTypes}")

		logging.debug(f">---- Done Preparing {this.name} ----<")

		runMessage = f"Building {this.name}"
		if (this.projectName):
			runMessage += f" for \"{this.projectName}\""
		if (this.projectType != this.projectName):
			runMessage += f", a \"{this.projectType}\""
		if (this.buildPath):
			runMessage += f"in {this.buildPath}"
		logging.info(runMessage)

		logging.debug(f"<---- Building {this.name} ---->")
		ret = this.Build()
		logging.debug(f">---- Done Building {this.name} ----<")

		this.PostBuild()
		return ret