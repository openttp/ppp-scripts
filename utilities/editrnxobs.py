#!/usr/bin/python3
#

#
# The MIT License (MIT)
#
# Copyright (c) 2018 Michael J. Wouters
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
# Manipulates RINEX observation files


import argparse
import datetime
import math
import os
import re
import shutil
import subprocess
import sys
import time

sys.path.append("/usr/local/lib/python3.6/site-packages")  # Ubuntu 18.04
sys.path.append("/usr/local/lib/python3.8/site-packages")  # Ubuntu 20.04
sys.path.append("/usr/local/lib/python3.10/site-packages") # Ubuntu 22.04

import ottplib as ottp
import rinexlib as rinex

VERSION = "2.2.2"
AUTHORS = "Michael Wouters"

# ------------------------------------------
def IsMJD(txt):
	return re.match(r'\d{5}',txt)

# ------------------------------------------
def ParseRINEXFileName(fname):
	ver = 0
	p = os.path.dirname(fname)
	match = re.search('(\w{4})(\d{3})0\.(\d{2})([oOdD]|[oOdD].\w{2,3})$',fname) # version 2 [dD] for Hatanaka compression
	if match:
		st = match.group(1)
		doy = int(match.group(2))
		yy = int(match.group(3))
		yyyy = yy
		if (yyyy > 80):
			yyyy += 1900
		else:
			yyyy += 2000
		ext = match.group(4)
		ver=2
		return (p,ver,st,doy,yy,yyyy,ext,'','','','','')
	
	match = re.search('(\w{9})_(\w)_(\d{4})(\d{3})(\d{4})_(\w{3})_(\w{3})_(\w{2})\.(\w{3}|\w{3}.\w{2,3})$',fname) # version 3
	if match:
		st = match.group(1)
		dataSource = match.group(2)
		yy = int(match.group(3))
		yyyy=yy
		doy = int(match.group(4))
		hhmm = match.group(5)
		filePeriod=match.group(6)
		dataFrequency=match.group(7)
		ft = match.group(8)
		ext = match.group(9)
		ver=3
		return (p,ver,st,doy,yy,yyyy,ext,dataSource,hhmm,filePeriod,dataFrequency,ft)
	
	ottp.ErrorExit(fname + ' is not a standard RINEX file name')
	
# ------------------------------------------
def ReadHeader(fin):
	# The header is returned as a list of strings,with line terminators retained
	hdr=[]
	readingHeader = True
	while readingHeader:
		l = fin.readline()
		hdr.append(l)
		#if (l.find('TIME OF LAST OBS') > 0):
		#	lastObs=l # extract time system
		if (l.find('END OF HEADER') > 0):
			readingHeader = False
	return hdr

# ------------------------------------------
def UpdateHeader(hdr,nsv):
	
	newHeader=[]
	i=0
	while i < len(hdr):
		if args.excludegnss:
			if 'SYS / # / OBS TYPES' in hdr[i]:
				if hdr[i][0] in args.excludegnss: # may have continuation lines
					nsats = hdr[i][3:6].strip() # don't cast to an integer: this may be whitespace
					if nsats:
						ncontinuation = int(math.ceil(int(nsats)/13)) - 1
					else:
						ncontinuation = 0
					i = i + 1 + ncontinuation
					continue
			if  'SYS / PHASE SHIFT' in hdr[i]:
				if hdr[i][0] in args.excludegnss: # may have continuation lines
					nsats = hdr[i][16:18].strip() # don't cast to an integer: this may be whitespace
					if nsats:
						ncontinuation = int(math.ceil(int(nsats)/10)) - 1
					else:
						ncontinuation = 0
					i = i + 1 + ncontinuation
					continue
			if '# OF SATELLITES' in hdr[i]:
				newHeader.append('{:6d}{:54}{:20}\n'.format(nsv,' ','# OF SATELLITES'))
				i = i + 1
				continue
			if 'GLONASS' in hdr[i][60:-1]:
				if 'R' in args.excludegnss:
					i = i + 1
					continue
		newHeader.append(hdr[i])
		i = i + 1
		
	return newHeader

# ------------------------------------------
def WriteTmpHeaderFile(tmpHeaderFile,hdr):
	try:
		fout = open(tmpHeaderFile,'w')
	except:
		ottp.ErrorExit('Unable to create temporary file ' + tmpHeaderFile)
		
	for l in hdr:
		fout.write(l)
	fout.close()
	
# ------------------------------------------ 
# Note that this will return multiple lines
#
def GetHeaderField(hdr,key):
	return [ l[0:60] for l in hdr if (l.find(key) == 60)]

# ------------------------------------------
# Note that newValue needs to correctly formatted

def ReplaceHeaderField(hdr,key,newValue):
	for li,l in enumerate(hdr):
		if (l.find(key) == 60):
			hdr[li]='{:<60}{:<20}\n'.format(newValue,key) # since other stuff assumes retention of the newline
			break

# ------------------------------------------
def AddHeaderComments(hdr,comments):
	# hdr and comments are lists
	# comments are inserted before any existing comments
	for li,l in enumerate(hdr):
		if (l.find('PGM / RUN BY / DATE') == 60):
			for ci,c in enumerate(comments):
				hdr.insert(li+1+ci,'{:<60}{:<20}\n'.format(c,'COMMENT'));
			break
		
# -------------------------------------------
def GetRinexVersion(hdr):
	vMajor = None
	vMinor = None
	hdrField = GetHeaderField(hdr,'RINEX VERSION / TYPE')
	if hdrField:
		match = re.search('(\d+)\.(\d+)',hdrField[0][0:9])
		if match:
			vMajor = int(match.group(1))
			vMinor = int(match.group(2))
			ottp.Debug('GetRinexVersion {:d}.{:02d}'.format(vMajor,vMinor))
	return [vMajor,vMinor]
	

# ------------------------------------------
def WriteRINEXFile(tmpDir,tmpDataFile,tmpHeaderFile,tmpRnxPath,srcRnxPath):
	
	# Contruct the temporary RINEX file in tmpDir
	ottp.Debug('Writing temporary RINEX ' + tmpRnxPath)
	with open(tmpRnxFile,'wb') as fout:
		with open(tmpHeaderFile,'rb') as f:
			fout.write(f.read())
		with open(tmpDataFile,'rb') as f:
			fout.write(f.read())
	
	# Clean up
	os.unlink(tmpHeaderFile)
	os.unlink(tmpDataFile)
	
	if srcRnxPath: # the new RINEX file originates from a single RINEX file, which we might be replacing
		ottp.Debug('Source RINEX file was ' + srcRnxPath)
		if args.replace:
			if args.backup: 
				fBackup = srcRnxPath + '.original'
				shutil.copyfile(srcRnxPath,fBackup)
				ottp.Debug('{} backed up to {}'.format(srcRnxPath,fBackup))
			shutil.copyfile(tmpRnxPath,srcRnxPath)
		else:
			if os.path.isdir(args.output):
				shutil.copy(tmpRnxPath,os.path.join(args.output,os.path.basename(srcRnxPath)))
			else:
				shutil.copyfile(tmpRnxPath,args.output) 
		os.unlink(tmpRnxFile) # bye bye
	else:
		if args.output: # this is how runcsrsppp.py calls this
			shutil.copy(tmpRnxPath,args.output) # works for both a destination directory/path
			os.unlink(tmpRnxFile) # bye bye
		else:
			ottp.Debug('WriteRINEXFile() - temporary file not renamed!')
			
	# This might leave tmpRnxFile in place -- see above
	
	ottp.Debug('Writing RINEX done')
		
# ------------------------------------------
# Main

appName= os.path.basename(sys.argv[0])+ ' ' + VERSION

examples =  'Usage examples\n'
examples += 'editnrxobs.py --catenate --excludeGNSS CRIJS --obsdir RINEX --template  \n'

parser = argparse.ArgumentParser(description='Edit a V3 RINEX observation file',
	formatter_class=argparse.RawDescriptionHelpFormatter,epilog = examples)

parser.add_argument('infile',nargs='+',help='input file or MJD',type=str)

parser.add_argument('--debug','-d',help='debug (to stderr)',action='store_true')

parser.add_argument('--catenate','-c',help='catenate input files',action='store_true')
parser.add_argument('--excludegnss','-x',help='remove specified GNSS (CEGRJI)',default='')
parser.add_argument('--fixmissing','-f',help='fix missing observations due to UTC/GPS day rollover mismatch',action='store_true')

parser.add_argument('--template',help='template for RINEX file names',default='')
parser.add_argument('--obsdir',help='RINEX file directory',default='./')
parser.add_argument('--tmpdir',help='directory for temporary files',default='./')

group = parser.add_mutually_exclusive_group()
group.add_argument('--output','-o',help='write output to file/directory',default='')
group.add_argument('--replace','-r',help='replace edited file',action='store_true')

parser.add_argument('--backup','-b',help='create backup (extension .original) of edited file',action='store_true')

parser.add_argument('--version','-v',action='version',version = os.path.basename(sys.argv[0])+ ' ' + VERSION + '\n' + 'Written by ' + AUTHORS)

args = parser.parse_args()

debug = args.debug
ottp.SetDebugging(debug)
rinex.SetDebugging(debug)

# Check arguments
if not(args.catenate or args.excludegnss):
	ottp.ErrorExit('Nothing to do!')

tmpDir =args.tmpdir
tmpDataFile =   os.path.join(tmpDir,'rnxmeas.tmp')
tmpHeaderFile = os.path.join(tmpDir,'rnxhdr.tmp')
tmpRnxFile = os.path.join(tmpDir,'rnx.tmp')

infiles = []

# Create a list of input files to process

# First, check for MJDs in the 'file list'
if (1==len(args.infile)):
	if IsMJD(args.infile[0]):
		mjdStart = int(args.infile[0])
		infiles.append(str(mjdStart)) # we will process MJDs later
		if args.fixmissing:
			mjdStop = mjdStart
			mjdStart = mjdStart -1
			infiles = [mjdStart] + infiles
elif (2==len(args.infile)):
	if IsMJD(args.infile[0]):
		mjdStart = int(args.infile[0])
		mjdStop = int(args.infile[1])
		if args.fixmissing:
			mjdStart -= 1
		if (mjdStop < mjdStart):
			ottp.ErrorExit('Stop MJD is before Start MJD')
		for m in range(mjdStart,mjdStop+1):
			infiles.append(str(m))
else:
	ottp.ErrorExit('Too many files!')
	
if infiles:
	if not(args.template):
		ottp.ErrorExit('You need to define a template for the RINEX file names (--template)')
	if not(rinex.MJDtoRINEXObsName(60000,args.template)):
		ottp.ErrorExit('Bad --template')
	for i in range(0,len(infiles)):
		fName = rinex.MJDtoRINEXObsName(int(infiles[i]),args.template)
		infiles[i] = os.path.join(args.obsdir,fName)
		#print(infiles[i])

# No ? Then check for a file sequence
if not(infiles):
	if (1==len(args.infile)):
		dirname = os.path.dirname(args.infile[0])
		if dirname == '' or dirname == '.':
			infiles.append(os.path.join(args.obsdir,args.infile[0]))
		else:
			infiles.append(os.path.join(args.infile[0]))
		if args.fixmissing:
			(path1,ver1,st1,doy1,yy1,yyyy1,ext1,dataSource1,hhmm1,filePeriod1,dataFrequency1,ft1)=ParseRINEXFileName(args.infile[0])
			date1=datetime.datetime(yyyy1, 1, 1) + datetime.timedelta(doy1 - 1)
			
	elif (2==len(args.infile)):
		
		(path1,ver1,st1,doy1,yy1,yyyy1,ext1,dataSource1,hhmm1,filePeriod1,dataFrequency1,ft1)=ParseRINEXFileName(args.infile[0]) # version here is naming convention
		(path2,ver2,st2,doy2,yy2,yyyy2,ext2,dataSource2,hhmm2,filePeriod2,dataFrequency2,ft2)=ParseRINEXFileName(args.infile[1])
		if not(path1 == path2):
			ottp.ErrorExit('The files must be in the same directory for sequences\n')
		if not(ver1 == ver2):
			ottp.ErrorExit('The RINEX files must have the same naming convention for sequences\n')
		if not(st1==st2):
			ottp.ErrorExit('The station names must match for sequences\n')
		if not(ft1==ft2):
			ottp.ErrorExit('The file types must match for sequencesn\n')
			
		if ((yyyy1 > yyyy2) or (yyyy1 == yyyy2 and doy1 > doy2)):
			ottp.ErrorExit('The files appear to be in the wrong order for sequences\n')
			
		# it appears we have a valid sequence so generate it
		if args.fixmissing:
			date1=datetime.datetime(yyyy1, 1, 1) + datetime.timedelta(doy1 - 2)
		else:
			date1=datetime.datetime(yyyy1, 1, 1) + datetime.timedelta(doy1 - 1)
		date2=datetime.datetime(yyyy2, 1, 1) + datetime.timedelta(doy2 - 1)
		td =  date2-date1
		
		# If the file does not have a leading path  then use args.obsdir
		if path1 == '' or path1 == '.':
			obsdir = args.obsdir
		else:
			obsdir = path1
		
		for d in range(0,td.days+1):
			ddate = date1 +  datetime.timedelta(d)
			if (ver1 == 2):
				yystr = ddate.strftime('%y')
				doystr=ddate.strftime('%j')
				fname = '{}{}0.{}{}'.format(st1,doystr,yystr,ext1) 
				infiles.append(os.path.join(obsdir,fname))	
			elif (ver1 == 3):
				yystr = ddate.strftime('%Y')
				doystr= ddate.strftime('%j')
				fname = '{}_{}_{}{:>03d}{}_{}_{}_{}.{}'.format(st1,dataSource1,yystr,int(doystr),hhmm1,filePeriod1,dataFrequency1,ft1,ext1) 
				infiles.append(os.path.join(obsdir,fname))
	else:
		ottp.ErrorExit('Too many files!')

#if (args.output and len(args.infile) >1 and not(args.catenate)):
	#if not(os.path.isdir(args.output)):
		#sys.stderr.write('The --output option must specify a directory  when there is more than one input file\n')
		#exit()

# The way we handle fixmissing is to catenate the files
# and then disassemble into the individual files. This is a simple way of ensuring that everything is in sequence.
# For this to work, we add the file previous to the first in the nominal sequence
# It will be rewritten as well, with entries belonging to the next day moved to the succeeding day
# Entries after the end of the day in the last file are not touched.

if args.fixmissing:
	args.catenate = True
	
# Preliminary stuff done.

# First, a quick check on RINEX version
fDe,algo = rinex.Decompress(infiles[0])
try:
	fin = open(fDe,'r')
except:
	ottp.ErrorExit('Checking RINEX version: unable to open ' + fDe)
	
hdr = ReadHeader(fin)
majorVer,minorVer = GetRinexVersion(hdr)
if (majorVer < 3):
	ottp.ErrorExit('RINEX version {:d} detected in {}. Only V3 is supported'.format(majorVer,infiles[0]))
rinex.Compress(fDe,infiles[0],algo)


# Now do stuff!
headers = []
svn     = []

if args.catenate: # open the measurement file for output
	try:
		fout = open(tmpDataFile,'w')
	except:
		ottp.ErrorExit('Unable to create temporary file ' + tmpDataFile)

compressionJobs =[]

for f in infiles:
	
	finName,algo = rinex.Decompress(f)
	compressionJobs.append([finName,f,algo])
	
	try:
		fin = open(finName,'r')
		svn=[]
	except:
		ottp.ErrorExit('Unable to open ' + finName)
	
	ottp.Debug('Opened ' + finName)
	
	if not(args.catenate): # open file for output of measurements
		try:
			fout = open(tmpDataFile,'w')
		except:
			ottp.ErrorExit('Unable to create temporary file ' + tmpDataFile)
			
	hdr = ReadHeader(fin)
	headers.append(hdr)
	
	reading = True
	while reading:
		l = fin.readline()
		
		if (len(l) == 0): #EOF
			reading = False
			continue
		
		rec = []
		if (l[0]=='>'):
			nmeas = int(l[32:36]) # cols 32-35
			epochFlag = int(l[31])
			rec.append(l)
			for m in range(0,nmeas):
				l = fin.readline()
				if epochFlag < 2: # there will be a SV identifier
					svid = l[0:3]
					if args.excludegnss:
						if not(l[0] in args.excludegnss):
							rec.append(l)
							if not(svid in svn): # this is SLOW
								svn.append(svid)
					else:
						rec.append(l)
						if not(svid in svn): # this is SLOW
							svn.append(svid)
				else:
					rec.append(l)
		else:
			pass # shouldn't happen I think FIXME should test!
		
		# Now output the measurement block
		if rec:
			if args.excludegnss: # may have to fix the measurement count
				fout.write(rec[0][0:32] + '{:3d}'.format(len(rec)-1) + rec[0][36:-1] + '\n')
			else:
				fout.write(rec[0])
			for i in range(1,len(rec)):
				fout.write(rec[i])
	
	if not(args.catenate): # writing individual files ...
		fout.close()
		newHdr = UpdateHeader(hdr,len(svn))
		AddHeaderComments(newHdr,['Processed by {}'.format(appName)])
		WriteTmpHeaderFile(tmpHeaderFile,newHdr)
		WriteRINEXFile(tmpDir,tmpDataFile,tmpHeaderFile,tmpRnxFile,finName)

if args.catenate:
	fout.close() # this is tmpDataFile
	
if (args.catenate and not(args.fixmissing)):
	
	# Write a new header, using the first as a template
	newHdr = UpdateHeader(headers[0],len(svn))
	AddHeaderComments(newHdr,['Processed by {}'.format(appName)])
	# Now we need to update the time of the last observation, but only do it if it's defined
	if GetHeaderField(newHdr,'TIME OF LAST OBS'):
		lastObs = GetHeaderField(headers[-1],'TIME OF LAST OBS') # remember, this returns a list
		ReplaceHeaderField(newHdr,'TIME OF LAST OBS',lastObs[0])
	
	WriteTmpHeaderFile(tmpHeaderFile,newHdr)
	WriteRINEXFile(tmpDir,tmpDataFile,tmpHeaderFile,tmpRnxFile,'') # empty name is used as a  flag
	
if args.fixmissing:
	
	fin = open(tmpDataFile,'r') # this is catenated data

	rolloverTOD = datetime.datetime(1980,1,6,0,0,0,tzinfo=datetime.timezone.utc) # no data before this !
	skipRead = False
	fileCount = 0
	reading = True
	
	for c in compressionJobs: 
		
		f = c[0] # decompressed file
		svn = [] 
		firstObs = []
		
		tmpDailyDataFile = os.path.join(tmpDir,'rnxdailymeas.{:d}.tmp'.format(fileCount))
		try:
			fout = open(tmpDailyDataFile,'w')
			ftmp = open(f,'r') # we know it exists
			hdr = ReadHeader(ftmp)
			ftmp.close() 
		except:
			ottp.ErrorExit('Unable to create temporary file ' + tmpDailyDataFile)
		fileCount += 1
		
		while reading:
		
			if not skipRead:
				l = fin.readline()
			skipRead = False
			if (len(l) == 0): #EOF
				break
			
			if (l[0]=='>'): # process a measurement block
				year = int(l[2:6])
				mon  = int(l[6:10])
				day  = int(l[9:13])
				hours= int(l[12:16])
				mins = int(l[15:19])
				secs = float(l[19:30])
				tod  = datetime.datetime(year,mon,day,hours,mins,int(secs),tzinfo=datetime.timezone.utc)
			
				if not firstObs:
					firstObs = [year,mon,day,hours,mins,secs]
					rolloverTOD = datetime.datetime(year,mon,day,tzinfo=datetime.timezone.utc) + datetime.timedelta(days=1)
		
				if (tod >= rolloverTOD and fileCount < len(infiles)): # time to write!
					#print(tod,rolloverTOD)
					skipRead = True # we want to process this line again for the new file
					break
				
				fout.write(l)
				
				nmeas = int(l[32:36]) # cols 32-35
				epochFlag = int(l[31])
				for m in range(0,nmeas):
					l = fin.readline()
					fout.write(l)
					if epochFlag < 2: # there will be a SV identifier
						svid = l[0:3]
						if not(svid in svn):
							svn.append(svid)
						
				lastObs = [year,mon,day,hours,mins,secs]
				
		fout.close()
		
		newHdr = UpdateHeader(hdr,len(svn))
		AddHeaderComments(newHdr,['Processed by {}'.format(appName)])
		hdrField = GetHeaderField(newHdr,'TIME OF FIRST OBS') # mandatory field
		timeSys = hdrField[0][48:51]
		ReplaceHeaderField(newHdr,'TIME OF FIRST OBS',
				'{:6d}{:6d}{:6d}{:6d}{:6d}{:13.7f}     {}'.format(firstObs[0],firstObs[1],firstObs[2],firstObs[3],firstObs[4],firstObs[5],timeSys))
		
		if GetHeaderField(newHdr,'TIME OF LAST OBS'):
			ReplaceHeaderField(newHdr,'TIME OF LAST OBS',
				'{:6d}{:6d}{:6d}{:6d}{:6d}{:13.7f}     {}'.format(lastObs[0],lastObs[1],lastObs[2],lastObs[3],lastObs[4],lastObs[5],timeSys))
		WriteTmpHeaderFile(tmpHeaderFile,newHdr)
		WriteRINEXFile(tmpDir,tmpDailyDataFile,tmpHeaderFile,tmpRnxFile,f)
		
				
	fin.close()
	
# ... and recompress anything we decompressed
for c in compressionJobs:
	rinex.Compress(c[0],c[1],c[2])

	
