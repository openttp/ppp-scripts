#!/usr/bin/python3

#
# The MIT License (MIT)
#
# Copyright (c) 2024 Michael J. Wouters
# 
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
# 
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
# 
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
#

# This provides a wrapper for running a csrs_ppp_auto job
# In particular it automates concatenation of RINEX files and post download cleanup etc
# Because this uses editrnxobs.py, only RINEX V3 is supported
# 

import argparse
import glob
import os
from   pathlib import Path
import re
import shutil
import subprocess
import sys
import time

# This is where ottplib is installed
sys.path.append("/usr/local/lib/python3.8/site-packages")  # Ubuntu 20.04
sys.path.append("/usr/local/lib/python3.10/site-packages") # Ubuntu 22.04

try: 
	import ottplib as ottp
except ImportError:
	sys.exit('ERROR: Must install ottplib\n eg openttp/software/system/installsys.py -i ottplib')

try: 
	import rinexlib as rinex
except ImportError:
	sys.exit('ERROR: Must install rinexlib\n eg openttp/software/system/installsys.py -i rinexlib')
	
VERSION = '0.4.0'
AUTHORS = 'Michael Wouters'
EDIT_RNX_OBS = 'editrnxobs.py'
CSRS_PPP_AUTO = 'csrs_ppp_auto.py'
RAPID_LATENCY = 2    # Latency of rapid orbit products 
WEEKLY_START = 60326  # MJD at which weekly processing starts (configurable) NOT the MJD on the file name!
MAX_AGE = 90          # maximum age of CSRS archive file
MAX_MISSING = 4 

# --------------------------------------------------------------------------------------------------------

home = os.environ['HOME'] 
root = home 
configFile = os.path.join(root,'etc','runcsrsppp.conf')
tmpDir = os.path.join(root,'tmp')
editRnxObs = EDIT_RNX_OBS
csrsPPPauto = CSRS_PPP_AUTO
weeklyStart = WEEKLY_START
runWeekly = False

parser = argparse.ArgumentParser(description='')

examples =  'Usage examples\n'
examples += '1. Do the weekly processing (assumes this is correctly scheduled by cron ie there is no test that it is the right day)\n'
examples += '    runcsrsppp.py\n'
examples += '2. Check for missing weekly files\n'
examples += '    runcsrsppp.py --missing\n'

parser = argparse.ArgumentParser(description='Generate a week long clock solution using CSRS PPP',
	formatter_class=argparse.RawDescriptionHelpFormatter,epilog=examples)

parser.add_argument('mjd',nargs = '*',help='first MJD [last MJD] (if not given, the MJD of two days ago is used as the last day of a 7 day run)')
parser.add_argument('--config','-c',help='use an alternate configuration file',default=configFile)
parser.add_argument('--debug','-d',help='debug (to stderr)',action='store_true')
parser.add_argument('--force','-f',help='force reprocessing',action='store_true')
parser.add_argument('--list','-l',help='list receivers',action='store_true')
parser.add_argument('--receivers','-r',help='comma separated list of receivers to process')
parser.add_argument('--version','-v',action='version',version = os.path.basename(sys.argv[0])+ ' ' + VERSION + '\n' + 'Written by ' + AUTHORS)
parser.add_argument('--missing',help='generate missing files',action='store_true')

args = parser.parse_args()

debug = args.debug
ottp.SetDebugging(debug)
rinex.SetDebugging(debug)

ottp.Debug('Using ottplib v{}'.format(ottp.LibVersion()))
ottp.Debug('Using rinexlib v{}'.format(rinex.LibVersion()))

configFile = args.config

if (not os.path.isfile(configFile)):
	ottp.ErrorExit(configFile + ' not found')

cfg=ottp.Initialise(configFile,['main:receivers','main:csrs user'])

if args.list:
	receivers = cfg['main:receivers'].split(',')
	print(f'Receivers in {configFile}:')
	for r in receivers:
		print(r)
	sys.exit(0)
	
if 'paths:root' in cfg:
	root = ottp.MakeAbsolutePath(cfg['paths:root'],home)

if 'paths:tmp' in cfg:
	tmpDir = ottp.MakeAbsolutePath(cfg['paths:tmp'],root)
if not(os.path.exists(tmpDir)):
	ottp.ErrorExit(tmpDir + " doesn't exist - check the configuration file")
	

if 'main:weekly start' in cfg:
	weeklyStart = int(cfg['main:weekly start'])

# Weekly files are named according to the starting MJD of the data within

mjdToday = ottp.MJD(time.time())
startMJD = weeklyStart + int((mjdToday - weeklyStart)/7 - 1 )*7 - RAPID_LATENCY
stopMJD  = startMJD + 6 # maximum of 7 days data
ottp.Debug('Default (weekly) MJD range: {} {}'.format(startMJD,stopMJD))

if (args.mjd): # specifying MJD overrides the automatic calculation
	
	if 1 == len(args.mjd): # one day only
		startMJD = int(args.mjd[0])
		stopMJD  = startMJD
	elif ( 2 == len(args.mjd)): # a range
		startMJD = int(args.mjd[0])
		stopMJD  = int(args.mjd[1])
		if (stopMJD < startMJD):
			ottp.ErrorExit('Stop MJD is before start MJD')
		if (stopMJD - startMJD > 7):
			ottp.ErrorExit('Time interval is too long')
	else:
		ottp.ErrorExit('Too many MJDs')


receivers = cfg['main:receivers'].split(',')
receivers = [r.lower() for r in receivers]

if args.receivers:
	newReceivers = args.receivers.split(',')
	newReceivers = [r.strip().lower() for r in newReceivers]
	# Do a basic check
	for rx in newReceivers:
		if not ((rx + ':' + 'rinex template') in cfg):
			print(f'{rx} does not appear to be defined in the configuration file')
			sys.exit(1)
	receivers = newReceivers
	
# What GNSS do we want
exclusions = ''
gnss = cfg['main:gnss'].upper()

if not('BEIDOU' in gnss):
	exclusions += 'C'

if not('GALILEO' in gnss):
	exclusions += 'E'

if not('GLONASS' in gnss):
	exclusions += 'R'
	
if not('GPS' in gnss):
	exclusions += 'G'

if not('IRNSS' in gnss):
	exclusions += 'I'

if not('QZSS' in gnss):
	exclusions += 'J'

ottp.Debug('Excluded GNSS = '+exclusions)

CSRSuser = cfg['main:csrs user']

for rx in receivers:
	
	# Concatenate RINEX into a single file
	
	template = cfg[rx + ':' + 'rinex template']
	obsDir   = ottp.MakeAbsoluteFilePath(cfg[rx + ':rinex dir'],root,os.path.join(root,'RINEX'))
	clockDir = ottp.MakeAbsoluteFilePath(cfg[rx + ':clock dir'],root,os.path.join(root,'ppp'))
	csrsDir  = ottp.MakeAbsoluteFilePath(cfg[rx + ':csrs dir'],root,os.path.join(root,'csrs'))
	station = cfg[rx + ':station']
	
	jobs = []
	
	ottp.Debug('Creating jobs for {}'.format(station))
	
	# Check the default job
	yy,doy,mon = ottp.MJDtoYYYYDOY(startMJD)
	clkfile = os.path.join(clockDir,'PPP{:02d}{:03d}{}.CLK'.format(yy % 100,doy,station))
	if args.force or not(os.path.exists(clkfile)):
		jobs.append([startMJD,stopMJD])
		ottp.Debug('{} is missing/reprocessing forced - added job [{:d}  {:d}]'.format(clkfile,startMJD,stopMJD))
	else:
		ottp.Debug('{} exists - skipped'.format(clkfile))
	if args.missing:
		prevMJD =  weeklyStart + int((mjdToday - weeklyStart)/7 - 2 )*7 - RAPID_LATENCY # start with previous week
		for i in range(0,MAX_MISSING):
			testMJD = prevMJD - i*7
			yy,doy,mon = ottp.MJDtoYYYYDOY(testMJD)
			clkfile = os.path.join(clockDir,'PPP{:02d}{:03d}{}.CLK'.format(yy % 100,doy,station))
			if args.force or not(os.path.exists(clkfile)):
				jobs.append([testMJD,testMJD + 6])
				ottp.Debug('{} is missing/reprocessing forced - added job [{:d} {:d}]'.format(clkfile,testMJD,testMJD + 6))
			else:
				ottp.Debug('{} exists - skipped'.format(clkfile))
	
	for job in jobs:
		
		jStartMJD = job[0]
		jStopMJD  = job[1]
		ottp.Debug('Processing {} for MJD {:d} - {:d}'.format(station,jStartMJD,jStopMJD))
		output   = os.path.join(tmpDir,'{}{:d}.obs'.format(station,jStartMJD)) # simple name for file which will be sent to CSRS PPP service
		gzoutput = output + '.gz'
		
		# Clean up messes
		if os.path.exists(output):
			os.unlink(output)
		if os.path.exists(gzoutput):
			os.unlink(gzoutput)
		
		# Create the concatenated, cleaned RINEX for processing
		
		# First, have to copy the RINEX files to the temporary directory
		# This is because we can't decompress somebody else's files in place
		# as this is not supported by editrnxobs at present
		# TO DO could be done ...
		
		tmprnx = [] # list of files we copied 
		for m in range(jStartMJD,jStopMJD+1):
			basename = rinex.MJDtoRINEXObsName(m,template)
			fname = os.path.join(obsDir,basename)
			files = glob.glob(fname+'*') # bodge to pick up a compressed file
			if len(files)==1:
				shutil.copy(files[0],tmpDir)
				rinex.Decompress(os.path.join(tmpDir,os.path.basename(files[0]))) # this will be deleted so no need to save details for recompression
				tmprnx.append(os.path.join(tmpDir,basename))
				
		ottp.Debug('Running ' + editRnxObs)
		
		try:
			cmdargs = [editRnxObs,'--catenate','--template',template,'--tmpdir',tmpDir,'--excludegnss',exclusions,'--obsdir',tmpDir,'--output',output,str(jStartMJD),str(jStopMJD)]
			x = subprocess.check_output(cmdargs) 
		except Exception as e:
			print(e)
			ottp.ErrorExit('Failed to run ' + editRnxObs)
		
		if not(os.path.exists(output)): # hmmm no output
			ottp.Debug('No RINEX found to process')
			for tr in tmprnx:
				os.unlink(tr)
			continue # not fatal
		
		# Compress it
		rinex.Compress(output,gzoutput,rinex.C_GZIP) # kludge for fOriginal ...
		
		tstart = time.time()
		
		# Submit job
		ottp.Debug('Running ' + csrsPPPauto)
		# This does not work reliably on our network
		# Try a few times before bombing out
		nTries = 1
		while (1):
			try:
				ottp.Debug(f'Attempt #{nTries}')
				cmdargs = [csrsPPPauto,'--user_name',CSRSuser,'--ref','ITRF','--get_max','90','--rnx',gzoutput,'--results_dir',tmpDir]
				x = subprocess.check_output(cmdargs)
				break
			except Exception as e:
				nTries += 1
				if nTries > 3:
					print(e)
					ottp.ErrorExit('Failed to run ' + csrsPPPauto)
				time.sleep(1)
				
		tstop  = time.time()
		ottp.Debug('Job run time = {:g} s'.format(tstop-tstart))
		
		# Extract the files we want
		ottp.Debug('Extracting ...')
		csrsout = output + '_full_output.zip'
		if not(os.path.exists(csrsout)):
			ottp.Debug(csrsout + ' is missing!')
			for tr in tmprnx:
				os.unlink(tr)
			continue # not fatal

		# FIXME check for the error file
		# Note though that this may just contain warnings
		
		try:
			cmdargs = ['unzip','-o',csrsout,'-d',tmpDir]
			x = subprocess.check_output(cmdargs) 
		except Exception as e:
			print(e)
			for tr in tmprnx:
				os.unlink(tr)
			ottp.ErrorExit('Failed to run unzip')
			
		# and put them in their proper place
		clkfile = os.path.join(tmpDir,'{}{:d}.clk'.format(station,jStartMJD))
		# for the output file, use the same naming convention as bernese
		
		yy,doy,mon = ottp.MJDtoYYYYDOY(jStartMJD)
		shutil.copyfile(clkfile,os.path.join(clockDir,'PPP{:02d}{:03d}{}.CLK'.format(yy % 100,doy,station)))
		
		# Keep the archive for a while, to help investigation of problems
		pathObj  = Path(csrsout) 
		shutil.move(csrsout,os.path.join(csrsDir,pathObj.name)) # force overwrite
		# Clean up temporary files
		files = glob.glob(os.path.join(tmpDir,'{}{:d}*'.format(station,jStartMJD)))
		for f in files:
			os.unlink(f)
		for tr in tmprnx:
			os.unlink(tr)
			
	# Remove old archive files so we're not keeping too much stuff
	ottp.Debug('Cleaning up archived CSRS files')
	tNow = time.time()
	files = glob.glob(os.path.join(csrsDir,'*.*'))
	for f in files:
		nDays = (tNow - os.path.getmtime(f))/86400.0
		ottp.Debug('Testing file {} - {:g} days old'.format(f,nDays) )# in days
		if nDays > MAX_AGE:
			ottp.Debug('Deleting ' + f)
			os.unlink(f)						 
