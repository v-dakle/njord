import argparse
import os, signal
import re
import requests
import sys
import time
import traceback
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# Measure runtime of the script
startTime = time.time()

# Set the global exit code variable
exitCode = 0 

# Initialize the first error var. It serves to decide whether to print an issue header or not. See the printNOK() function for details.
firstError = True

# Set up named arguments
parser=argparse.ArgumentParser()
parser.add_argument('-d', '--domain', help='Base URL of your portal, including the protocol, e.g. https://docs.example.org', required=True)
parser.add_argument('-f', '--folder', help='Folder (subportal) on your website, e.g. tutorials', required=False, default="")
parser.add_argument('-x', '--no-external', help="Do not check external pages (ie. pages outside the domain)", required=False, action='store_true')
parser.add_argument('-s', '--sitemap', help='Specify sitemap URL manually. Useful for checking only a portion of the portal using the --domain argument. Use full URL.', required=False, default="")
parser.add_argument('-q', '--quiet', help='Do not print warnings, only info and errors.', required=False, action='store_true')
parser.add_argument('-v', '--verbose', help='Be very verbose and print time spent on each (larger) operation. Warning: The function of this debugging switch is not actively maintained.', required=False, action='store_true')

# Store the arguments' values
args=parser.parse_args()
domain = args.domain
folder = args.folder
manualSitemapLoc = args.sitemap
noExternal = args.no_external
beQuiet = args.quiet
beVerbose = args.verbose

# Domain and folder parameters cleanup:
#	If domain doesn't start with HTTP(S) protocol, add it:
if not re.match(r'https?://', domain):
	domain = "https://" + domain

# 	If domain ends with slash, remove the slash.
if domain[-1] == '/':
	domain = domain[:-1]

# 	Folder isn't mandatory, first check if it exists.
# 	If 'folder' doesn't begin with slash, add it.
if folder:
	if folder[0] != '/':
		folder = "/" + folder

	# 	If 'folder' ends with slash, remove the slash.
	if folder[-1] == '/':
		folder = folder[:-1]

# 	If 'folder' is empty, initialize it with an empty string. (Unset var can't be concatenated with another string, such as 'domain'.)
else:
	folder = ""

# Oftentimes, it's easier to work with the whole path.
URLPath = domain + folder

# Prepare a session for requests module and initialize headless Firefox.
# Headless Firefox is used to check validity of anchors. Requests are used to check validity of normal links.
# Why not use one for both tasks? 
# 	Requests are much faster and easier to work with but they fail to render the whole content of certain pages which results in missing cca 1k of links to check.
# 	Headless Firefox is (as any web driver) very slow and resource hungry. However, we need it to get cumbersome JS-generated pages like KL MAPI reference which requests module can't handle. On the other hand, the webdriver's API doesn't return HTTP codes, so we can't use it for links validity, for that's based on the return codes.

# Requests
# 	According to the docs, using sessions can significantly improve performance -- "if you’re making several requests to the same host, the underlying TCP connection will be reused"
# 	https://requests.readthedocs.io/en/latest/user/advanced/#session-objects
# 	Potentially useful for setting up retry policy: https://stackoverflow.com/a/47475019/2216968 (from requests.adapters import HTTPAdapter and from urllib3.util.retry import Retry)
sessionForRequests = requests.Session()

# 	Add an accepted user agent to the Sessions so certain pages (like prerender.io) don't block the requests with 403 or close the connection without a response altogether:
# 	sessionForRequests.headers.update({'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:12.0) Gecko/20100101 Firefox/12.0'})
sessionForRequests.headers.update({'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/51.0.2704.103 Safari/537.36'})

# Headless Firefox
# 	Initialize headless Firefox browser.
# 	The initialization and generally page processing can take VERY long time (couple of seconds for the init and then per request).
opts = FirefoxOptions()
opts.add_argument("--headless")
browser = webdriver.Firefox(options=opts)
# Optional for possible future reference. The executable_path isn't mandatory if geckodriver is in ${PATH}.
# browser = webdriver.Firefox(options=opts, executable_path='/usr/bin/geckodriver')

# Define colors. 
# If the OS we're on is Windows, drop that and just fill the variables with empty strings. Handling colors in Windows command prompt isn't worth the effort.
if sys.platform != 'win32':
	class color:
		PURPLE = '\033[95m'
		CYAN = '\033[96m'
		DARKCYAN = '\033[36m'
		BLUE = '\033[94m'
		GREEN = '\033[92m'
		YELLOW = '\033[93m'
		RED = '\033[91m'
		BOLD = '\033[1m'
		UNDERLINE = '\033[4m'
		END = '\033[0m'
else:
	class color:
		PURPLE = ''
		CYAN = ''
		DARKCYAN = ''
		BLUE = ''
		GREEN = ''
		YELLOW = ''
		RED = ''
		BOLD = ''
		UNDERLINE = ''
		END = ''

# Function that prints all the errors and warnings. It consumes:
# 	the page (title & URL) an issue is on (var page), 
# 	boolean telling whether it's the first error for the page (var first), 
#	type of the error (var type),
#	whether the error is on redirection (var redir, not used anymore)
#	the HTTP error code of the issue (for non-anchor links, var errorCode).
def printNOK(pg="", ln="", first=False, type=None, redir="", errorCode=""):
	# exitCode is global variable so we must declare here that we want to work with the global var, not its local instance inside this function
	# exitCode is set to 1 (True) in case of errors, and it's left unchanged (with 0 (False) as the default) for warnings. If the whole script finishes with exitCode==0, Azure is happy and green.
	global exitCode
	internalExitCode = exitCode

	# Decide whether to print the headline (which page has issues). The logic behind this is:
	# 	We print the headline only if it's the 1st error for the page. 
	# 	But if the 'quiet' switch is on and the issue is only warning, then we mustn't print the headline because it'd be only headline and no issue beneath it (we don't print warnings when we're told to be quiet).
	# POZNAMKA - temporarily disable absorel warning altogether, until Bara et al. finalize the way URLs are generated
	if first == True  \
		and not (beQuiet == True  \
				  and (   type == 'absorel'  \
					   or type == 'unreachable'  \
					   or type == 'cantGoOutside'  \
					   or type == 'externalNOK')  \
				  or type == 'sitemapNotFound' \
				  or type == 'absorel' \
				):
		if pagesLinksAndAnchors:
			print("Issues in " + color.BOLD + pagesLinksAndAnchors[page]['title'] + color.END + " (" + pg + ")")
		else:
			print("Some intial generic error occurred")

	# Outside page threw 404
	if type == '404':
		print(color.RED + color.BOLD + "##vso[task.logissue type=error] Outsite anchor link unreachable: " + color.END + ln)
		internalExitCode = 1
		first = False

	# Remote sitemap not found
	elif type == 'sitemapNotFound':
		print(color.RED + color.BOLD + "##vso[task.logissue type=error] Remote sitemap not found under this URL (manually set or autogenerated as `domain + folder + sitemap.xml`): " + color.END + ln)
		internalExitCode = 1
		first = False

	# Internal page threw 404
	elif type == 'internalSitemap404':
		print(color.RED + color.BOLD + "##vso[task.logissue type=error] URL in the sitemap unreachable: " + color.END + ln)
		internalExitCode = 1
		first = False

	# Can't perfom initial processing of a page
	elif type == 'cantProcessPage':
		print(color.RED + color.BOLD + "##vso[task.logissue type=error] Can't process (find title and links) this page: " + color.END + ln)
		internalExitCode = 1
		first = False

	# The sitemap doesn't contain any URL that matches the 'domain'+'folder' combination.
	elif type == 'noSitemapMatch':
		print(color.RED + color.BOLD + "##vso[task.logissue type=error]No page URL in the sitemap matches the URL path you've entered: " + color.END + URLPath)
		internalExitCode = 1
		first = False

	# Internal (relative within the portal) link is constructed in an absolute manner which is suboptimal
	elif type == 'absorel':
		pass
		# if beQuiet == False:
		# 	# POZNAMKA: TEMPORARILY COMMENTING THIS BECAUSE OF OPENAPI ISSUE
		# 	print(color.YELLOW + color.BOLD + "##vso[task.logissue type=warning] WARNING: relative link with absolute URL: " + color.END + ln)
		# 	first = False

	# Anchor link looks like internal but we didn't download it (wasn't in the sitemap?). This error can easily be circumvented by doing additional GET, but it's a nice check for a random sitemap error.
	elif type == 'unreachable':
		if beQuiet == False:
			# Add the final link destination URL only if it's different from the original URL found in the current page. 
			# Printing only the redirect wouldn't be helpful because you wouldn't then find it in the page.
			if link != redir:
				redirNote = " (Redirects to: " + redir + " )"
			else:
				redirNote = ""
			print(color.YELLOW + color.BOLD + "##vso[task.logissue type=warning] Can't check anchor validity:" + color.END + " the target page not in current DB (probably wasn't in the sitemap). Link: " + link + redirNote)
			first = False

	# Outside link isn't in DB and 'no-external' is True, so we can't fetch the page to check it (obviously...)
	elif type == 'cantGoOutside':
		if beQuiet == False:
			print(color.YELLOW + color.BOLD + "##vso[task.logissue type=warning] Can't check anchor validity:" + color.END + " the target page not in current DB and you forbade me to probe pages outside domain + folder (-x switch). Link: " + ln)
			first = False

	# External broken anchor, we're treating it as a warning only
	elif type == "externalNOK":
		if beQuiet == False:
			print(color.YELLOW + color.BOLD + "##vso[task.logissue type=warning] External anchor doesn't seem to exist: " + color.END + ln)
			first = False

	# External normal link either didn't resolve or timed out. We know the HTTP error code, so we treat it as an error.
	elif type == "normalLinkUnreachable":
		print(color.RED + color.BOLD + "##vso[task.logissue type=error] Link unreachable (HTTP code " + str(errorCode) + "): " + color.END + ln)
		internalExitCode = 1
		first = False

	# We failed to get a normal link and don't know the HTTP error code, hence treating this as a warning (it could be a working site that just hates us)
	elif type == "normalLinkUnresolved":
		print(color.YELLOW + color.BOLD + "##vso[task.logissue type=warning] URL resolution or time-out error. Manual check advised (HTTP code " + str(errorCode) + "): " + color.END + ln)
		first = False

	# Anchor (ID) isn't in the target page (error type for this function is unspecified)
	else:
		print(color.RED + color.BOLD + "##vso[task.logissue type=error] Anchor NOK: " + color.END + ln)
		internalExitCode = 1
		first = False

	exitCode = internalExitCode
	return first

# Set up the variables
absorel = 0
checkedLinks = {}
linksInSitemap = 0
missed = 0
nokAnchorOutside = 0
nokInPage = 0
nokInternal = 0
notInSitemap = 0
okAnchorOutside = 0
okInPage = 0
okInternal = 0
okNormalLinks = 0
pagesChecked = 0
pagesLinksAndAnchors = {}
retrieved = 0
sitemapFail = False
unreachable = 0
wasAbsorel = {}
# Notes about pagesLinksAndAnchors:
#	 Prepare dictionary with the following structure (each URL is a page from the sitemap). This is the main DB we're working with:
#		 pagesLinksAndAnchors
#		 {
#		 	"URL": {
#		 		"title": "Lipsum",
#		 		"anchor-links": [ "https://example.org/page-1#anchor01", "intrapage-anchor" ],
#		 		"normal-links": [ "https://example.org/page-3", "https://3x4mple.org/page-4" ],
#	 			"anchors": ["anchor-1", "anchor-2"]
#		 	}
#		 }

# Note about wasAbsorel
#	 Set up a dictionary for absolute links within the portal. We want to report these as suboptimal.

# Function to print stats when the script finishes
def printStats():
	print(color.BOLD + "\n===== STATS ===== " + color.END + \
		  " \nTotal URLs in sitemap: " + str(linksInSitemap) + \
		  " \nTotal pages the DB: " + str(len(pagesLinksAndAnchors)) + \
		  " \nTotal pages checked: " + str(pagesChecked) + \
		  "\nAnchor links: " + \
		  "\n\tOK - in-page: " + str(okInPage) + \
		  "\n\tOK - internal: " + str(okInternal) + \
		  "\n\tNOK - in-page: " + str(nokInPage) + \
		  "\n\tNOK - internal: " + str(nokInternal) + \
		  "\n\tOK outside portal: " + str(okAnchorOutside) + \
		  "\n\tNOK outside portal: " + str(nokAnchorOutside) + \
		  "\nOK - normal links: " + str(okNormalLinks) + " (" + str(len(checkedLinks)) + " unique)" \
		  "\nUnreachable links: " + str(unreachable) + \
		  "\nAbsolute URLs within Domain: " + str(absorel) + \
		  "\nPages not in sitemap: " + str(notInSitemap) + \
		  "\n\nScript execution time: " + str(round(time.time() - startTime)) + " sec.")

# Function used when the 'verbose' parameter is True. Prints how long it took from the last invokation (provided the last invokation was called as `debugTime = printDebugTime("bla bla", debugTime, startTime)`)
def printDebugTime(message, blockStartTime, scriptStartTime):
	print(message + " " + str(round(time.time() - blockStartTime)) + " sec. (" + str(round(time.time() - scriptStartTime)) + " since start)")
	return time.time()

def finishAndQuit(errCode=0, browser=None):
	# Finished, close temporary headless Firefox and print statistics.
	print("Finished...")
	if browser:
		print("... exiting the browser...")
		browser.quit()
		printStats()

	print("... and closing.")
	# Make this try/except block active only when you deploy Njord to Azure
	try:
		tasklist = os.popen('tasklist /v').read().strip().split('\n')
		name = "geckodriver.exe"
		for process in tasklist:
			if name in process:
				os.system("taskkill /im %s /f" %(name))
				print("Process " + str(process) + " Successfully terminated.")
	except: 
		print("Error Encountered while terminating geckodriver.exe sub-processes.")
		traceback.print_exc()
		errCode = 1

	sys.exit(errCode)

if beVerbose:
	debugTime = printDebugTime("Initialization took", startTime, startTime)

# Sign in the browser so that we can get ends of articles, as well as the protected pages.
# 	The sleep times are required because the login form is slow.
# 	I should put this to try/except block, but it can survive here like this, too.
URL_login = "https://kontent.ai/learn/api/auth/login"
browser.get(URL_login)
time.sleep(10)

# These waits are most likely useless because we need to wait using the time.sleep() anyway, but they may avert some issues.
loginEmail = WebDriverWait(browser, 10).until(
    EC.element_to_be_clickable(browser.find_element(By.NAME, "email"))
)
loginPassword = WebDriverWait(browser, 10).until(
    EC.element_to_be_clickable(browser.find_element(By.NAME, "password"))
)

# This is a trial account created using a temp mail. It doesn't have access anywhere except the standard Getting started project.
loginEmail.send_keys("REDACTED")
loginPassword.send_keys("REDACTED")

# Submit the form and wait
browser.find_element(By.NAME, "submit").click()
time.sleep(10)

# Test whether the sign-in succeeded -- load a test page and see if we get "complete the lessons" instead of the "sign in first" error.
URL_test = "https://kontent.ai/learn/create/walkthrough-for-content-creators/test"
browser.get(URL_test)
document = browser.page_source

if "Complete the lessons in this path to unlock the test." in document:
	print("OK, Njord is signed into KAI Learn.")
else:
	print("Njord failed to sign in, cya when I cya.")
	exitCode = 1
	finishAndQuit(exitCode, browser)

# Get rid of the cookie bar -- accept all cookies
# This is needed mainly becuase of the clicking interaction in the Product updates page (pagination loading).
KLrandomPage = "https://kontent.ai/learn/" # this can be any KL page
browser.get(KLrandomPage)
time.sleep(5)
try:
	cookieButton = browser.find_element(By.XPATH, "//button[@class='ch2-btn ch2-allow-all-btn ch2-btn-primary']")
	cookieButton.click()
	time.sleep(1)
	print("All cookies accepted.")
	
except Exception:
	print("The cookie accept button not found. Details in the exception:")
	traceback.print_exc()

try:
	# Get the sitemap
	# Open locally saved sitemap
	# sitemap=str(open("sitemap.xml", "r").read())

	# Get the whole live sitemap, read the HTTP object and convert it to string. Requires import requests.
	# Tips for KKD sitemap locations:
		# Preview: https://kcd-web-preview-master.azurewebsites.net/learn/sitemap.xml
		# Live: https://kontent.ai/learn/sitemap.xml
	if manualSitemapLoc:
		print("Getting the remote sitemap from manually selected location: " + manualSitemapLoc)
		try:
			sitemapReq = sessionForRequests.get(manualSitemapLoc, timeout=20)
			if sitemapReq.status_code < 400:
				sitemap = sitemapReq.text
			else:
				firstError = printNOK("", manualSitemapLoc, True, "sitemapNotFound")
				sitemapFail = True
		except:
			firstError = printNOK("", manualSitemapLoc, True, "sitemapNotFound")
			sitemapFail = True

	else:
		# Automatically guess sitemap location. It should domain+folder+'/sitemap.xml' according to https://www.sitemaps.org/protocol.html#location)".
		sitemapURL = domain + folder + "/sitemap.xml"
		print("Getting the remote sitemap. Assuming it's at " + sitemapURL + "\n") 
		try:
			sitemapReq = sessionForRequests.get(sitemapURL, timeout=20)
			if sitemapReq.status_code < 400:
				sitemap = sitemapReq.text
			else:
				firstError = printNOK("", sitemapURL, True, "sitemapNotFound")
				sitemapFail = True
		except:
			firstError = printNOK("", sitemapURL, True, "sitemapNotFound")
			sitemapFail = True

	if sitemapFail:
		finishAndQuit(exitCode, browser)

	if beVerbose:
		debugTime = printDebugTime("Getting the sitemap took", debugTime, startTime)

	# Find all URLs with the URLPath in the sitemap file. "rf" in findall meaninings: r=regex, f=allow variables in the string searched for. 
	# Regexes (and all the match, search, and findall) require import re.
	URLs = re.findall(rf'({URLPath}.*?)</loc>', sitemap)
	linksInSitemap = len(URLs)

	if beVerbose:
		debugTime = printDebugTime("Parsing the URLs in the sitemap took", debugTime, startTime)

	# Go thru the URLs obtained from the sitemap and get anchor links and IDs from them so we can check them later.
	for URL in URLs:
		retrieved += 1

		# Get the document source
		# Just a reminder: browser is an instance of headless Firefox. sessionForRequests is an instance of requests.
		try:
			browser.get(URL)
			# Wait 4 more seconds until atrocities like Management API v2 process all the JS
			# Try implementing it using this guide: https://stackoverflow.com/a/26567563 (condition: wait for "gatsby-announcer" instead of "IdOfMyElement")
			if "reference" in URL:
				time.sleep(4)
			
			# Product updates are JS-paginated, we need to load the whole page "manually" by clicking the "Show more" button until it's there no more (the while loop)
			elif ( "learn/product-updates" in URL ):
				time.sleep(2)
				print("Getting product-updates page.")
				def getNextProductUpdatesPage():
					try:
						nextButton = browser.find_element(By.XPATH, "//button[@class='button_button__u6okP']")
						nextButton.click()
						return True
					except:
						return False

				nextPageExists = True
				i = 0
				while nextPageExists:
					nextPageExists = getNextProductUpdatesPage()
					time.sleep(2)
					i += 1
				print("Reached the end of product updates. Clicked the 'Show more' button " + str(i) + " times.")
			else:
				time.sleep(1)

			document = browser.page_source
		
		except:
			firstError = printNOK("", URL, firstError, "internalSitemap404")

		# Try to get all the pages data and links. If anything in here fails, drop the whole page.
		try:
			# Get the document's <title>
			title = re.search(rf'<title.*?>(.*?)</title>',document).group(1)

			# Prepare sub-dictionary for the document. See details about the structure above the initiation of the dictionary
			pagesLinksAndAnchors[URL] = {}
			pagesLinksAndAnchors[URL]['title'] = title

			# Get links with anchors (hash followed by at least one character except for a closing quote) in the document
			anchorLinks = re.findall(rf'href="([^"]*?#[^\"]+?)"', document)

			# Get non-anchor links (opening quotes followed by at least one character and the link doesn't contain a hash) in the document
			normalLinks = re.findall(rf'<a [^>]*?href="([^"#]+?)"', document)

			# Assign the normal links to the main DB of documents w/ links inside them now, as they don't need further cleaning (unlike anchors)
			pagesLinksAndAnchors[URL]['normal-links'] = normalLinks

			# Cleaning up some anchor mess. Delete:
			#   Anchor links that are term definitions inside term definition bubbles (start with "#term-definition-term_"). They're causing false positives since there's no heading with such an ID (https://kontent-ai.atlassian.net/browse/CTC-1009).
			#   All links to app.diagrams.net and viewer.diagrams.net because they're wicked and cause false positives (they contain anchor character but they don't lead to any anchor in the target document).
			# 	DAPI links are sometimes not anchor links (#tag, #section, #operation)
			#   The "#main" and "#subscribe-breaking-changes-email" anchor links because they're just internal bullshitery in KL.
			#	Intra-page anchor links in Dev cert -- the page is in the sitemap but it's locked for unsigned users (i.e., Njord) -- this should be fixed by not including the page in the sitemap
			#   All GitHub links, as GH apparently uses JS instead of the standard HTML way to navigate to the correct place.
			#   Postman app links -- the hash character doesn't mean it's an anchor
			#   Postman learn -- seems the document is JS generated so we can't effectively check anything. Also, Postman likes to ban us.
			# 	Product updates -- see above in the beginning of this loop
			#   Links longer than 2048 character. These are quite probably some weirdness like links to diagram.net that have the whole diagram encoded into the URL, apparently. The hash character there doesn't stand for an anchor anyway..
			# Note: We can safely do this in one loop because the loop contains only one condition block and the i var will never get incremented unless nothing gets deleted..
			# Note: Deleting an item from an array mutates the existing array.
			i = 0
			while i < len(anchorLinks):
				if (\
						re.match("#term-definition-term_", anchorLinks[i]) \
					 or re.match("#main", anchorLinks[i]) \
					 or re.match("#subscribe-breaking-changes-email", anchorLinks[i]) \
					 or re.match("https://app.diagrams.net", anchorLinks[i]) \
					 or re.match("https://app.getpostman.com/run-collection", anchorLinks[i]) \
					 or re.match("https://github.com", anchorLinks[i]) \
					 or re.match("https://kontent.ai/learn/develop/developer-certification/before-you-start", anchorLinks[i]) \
					 or re.match("https://kontent.ai/learn/docs/apis/openapi/delivery-api/#operation", anchorLinks[i]) \
					 or re.match("https://kontent.ai/learn/docs/apis/openapi/delivery-api/#section", anchorLinks[i]) \
					 or re.match("https://kontent.ai/learn/docs/apis/openapi/delivery-api/#tag", anchorLinks[i]) \
					 or re.match("https://kontent.ai/learn/product-updates", anchorLinks[i]) \
					 or re.match("https://learning.postman.com/", anchorLinks[i]) \
					 or re.match("https://viewer.diagrams.net", anchorLinks[i]) \
					 or len(anchorLinks[i]) > 2048 \
					):
					del anchorLinks[i]
				else:
					# And while we're at it, if the link isn't deleted:
					# 	Check for absolute links within the domain. (start with the domain instead of just slash). If found, save it to the 'wasAbsorel' array. We'll warn about them later.
					# 	Prepend relative links with the 'domain' to make them absolute for further use.
					if re.match(domain, anchorLinks[i]):
						if not URL in wasAbsorel:
							wasAbsorel[URL] = []
						wasAbsorel[URL].append(anchorLinks[i])
						absorel += 1
					anchorLinks[i] = re.sub(rf'^/', domain + '/', anchorLinks[i])
					i += 1

			# Assign cleaned anchor links to the main DB
			pagesLinksAndAnchors[URL]['anchor-links'] = anchorLinks

			# Get HTML ID attributes in the document
			anchors = re.findall(rf'id="(.*?)"', document)
			pagesLinksAndAnchors[URL]['anchors'] = anchors

			if beVerbose:
				debugTime = printDebugTime("Getting the " + URL + " took", debugTime, startTime)

		except Exception:
			firstError = printNOK("", URL, firstError, "cantProcessPage")
			print("The exception:")
			traceback.print_exc()

	if retrieved == 0:
		firstError = printNOK(type="noSitemapMatch")

	# Now, we go thru each page (URL) we got in the main DB and for each page:
	# 	1/ Check if the anchor links inside it are valid
	# 	2/ Check if the normal links inside it are valid
	for page in pagesLinksAndAnchors:
		
		# This is indicator whether we're printing the 1st error for the current page. If yes, then print the page title and URL. If not, don't print that, just print the error.
		firstError = True

		# We want to report broken (normal) links only once per page, even if there more of their instances -> this is a DB of NOK links within the page
		nokLinkMultiCheck = []

		# Check anchor links within the current page
		for link in pagesLinksAndAnchors[page]["anchor-links"]:

			# Inform user the link is absolute even though it's within the domain
			try:
				if link in wasAbsorel[page]:
					firstError = printNOK(page, link, firstError, "absorel")
			except:
				# It isn't. Nothing to report.
				pass

			# If the link is an in-page anchor link
			if re.match('#', link):
				if link.replace("#", "") in pagesLinksAndAnchors[page]["anchors"]:
					okInPage += 1
				else:
					firstError = printNOK(page, link, firstError)
					nokInPage += 1

			# If the link is an anchor link to another page inside the portal
			elif re.match(f'{URLPath}', link):
				# Split the link to the base link and the anchor by the hash character. 
				# Also remove potential trailing slash between the base URL and the anchor
				linkBaseURL = re.search(fr'(.*?)/?#', link).group(1)
				linkAnchor = re.search(fr'#(.*)', link).group(1)

				# See if we have the link in the internal DB; if yes, see if the anchor is in the target page
				if linkBaseURL in pagesLinksAndAnchors:
					if linkAnchor in pagesLinksAndAnchors[linkBaseURL]['anchors']:
						okInternal += 1
					else:
						firstError = printNOK(page, link, firstError)
						nokInternal += 1

				# See if it isn't just a redirect
				# requests.get() gets history with return HTTP codes, .url contains the final landing URL. Requires import requests
				else:
					finalURL = requests.get(linkBaseURL).url
					time.sleep(2)

					# See if the final URL is in the internal DB.
					if finalURL in pagesLinksAndAnchors:
						if linkAnchor in pagesLinksAndAnchors[finalURL]['anchors']:
							okInternal += 1
						else:
							firstError = printNOK(page, link, firstError, None, finalURL)
							nokInternal += 1
					# This branch should never occur. If it does, it either means 
					# that the page is in our (sub)portal (domain + folder) but it isn't in the sitemap (incomplete sitemap), 
					# or the sitemap contains URLs with query string parameters,
					# or there's a URL structure Njord isn't ready for, 
					# or it's a bug in Njord. 
					else:
						firstError = printNOK(page, link, firstError, "unreachable", finalURL + "#" + linkAnchor)
						notInSitemap += 1

			# The link leads outside the (sub)portal.
			# Let's download the page and see if the anchor exists in the target page (but only if user didn't prohibit this by the -x switch).
			else:
				if noExternal == False:
					# Try whether the page exists, if it does, test it. Otherwise, assume 404 or other error killed the try block. We don't care, it's simply unreachable.
					try:
						# Get the outside page
						browser.get(link)
						outsidePage = browser.page_source

						# Get base URL and anchor from the link
						outsideLinkBaseURL = re.search(r'(.*?)#', link).group(1)
						outsideLinkAnchor = re.search(r'#(.*)', link).group(1)
						
						# Try the normal anchor system - anchors go to IDs in HTML
						if re.search(rf'id="{outsideLinkAnchor}"', outsidePage):
							okAnchorOutside += 1

						# Anchor not found. The anchor is either some 3rd-party atrocity, or broken.
						# (But it's external so we don't treat it as an error, because it might be some of those 3rd-party bullshiteries.)
						else:
							firstError = printNOK(page, link, firstError, "externalNOK")
							nokAnchorOutside += 1
					# Apparently the page doesn't exist (other possible issues can be 403, 500 or other codes >399, we don't really care what exactly it is)
					except:
						firstError = printNOK(page, link, firstError, ">399")
						unreachable += 1

				# We've been prohibited from going outside the domain+folder -> inform only that we can't check the page.
				else:
					firstError = printNOK(page, link, firstError, "cantGoOutside")

		if beVerbose:
			debugTime = printDebugTime("Processing anchor links for " + page + " took", debugTime, startTime)

		# Check normal (non-anchor) links within the current page
		for link in pagesLinksAndAnchors[page]["normal-links"]:

			# If the link is relative, add the domain to it to make it absolute including the protocol.
			if re.match(r'^/', link):
				link = domain + link

			# This is a very dirty fix for this bug: [KST-762] KL pages with video contain invalid (404) link to themself but with missing `learn` folder - Kontent.ai Jira (https://kontent-ai.atlassian.net/browse/KST-762)
			# If the link ends the same as the current page && is missing the `/learn` part (`https://kontent.ai` == 18 chars and `https://kontent.ai/learn` == 24 chars), we don't need to check it -- it's invalid, but if we fixed it to contain the `/learn` part, it'd be the same as the current page which is obviously valid. Hence, we skip this iteration.
			if link[18:] == page[24:]:
				continue

			# Remove some links from the testing:
			#	Fonts (local and those on cdnfonts, because CDNFonts returns 403 even if the URL is valid)
			#	Random example / showcase domains or parts of URLs
			#   Azure throws requests into exception block
			#	Codepen.io blocks Requests entirely with 403
			#	KKD PDF export
			# 	`auth/login?returnTo` is the "Continue" button which leads to the sign-in form for unsigned users
			# 	Zapier.com is OK with headless Firefox but refuses requests library, Twitter throws 400 to requests, Adobe 403
			#	Cloudflare refuses everything, possible solution is another webdriver: https://stackoverflow.com/questions/68289474/selenium-headless-how-to-bypass-cloudflare-detection-using-selenium
			#	player.vimeo.com throws 404, it's used in code samples (which I can't easily separate)
			#	https://business.adobe.com times out on bots (it looks for more than just a user agent, similar to Zapier or Cloudflare).
			#	'%7B' is URL-encoded curly bracket '{' -- used in example URLs to encapsulate variables ('{var}')
			# Note about speed (from fastest to slowest): re.match('') > re.match(r'') > re.search('') > re.search(r'')
			#	It seems they're all regex because I need to escape question marks even if I don't add the `r` prefix.
			if not ( \
						re.match('blob:https://kontent.ai', link) \
					or  re.match('http://docs.oasis-open.org/xliff/xliff-core', link) \
					or  re.match('https://assets-us-01.kc-usercontent.com', link) \
					or  re.match('https://azure.microsoft.com/en-us', link) \
					or  re.match('https://business.adobe.com/products/target', link) \
					or  re.match('https://csrc.nist.gov/Projects/key-management/key-management-guidelines', link) \
					or  re.match('https://graphiql-online.com/', link) \
					or  re.match('https://help.zapier.com/hc/en-us/articles', link) \
					or  re.match('https://player.vimeo.com/video/', link) \
					or  re.match('https://twitter.com', link) \
					or  re.match('https://www.cloudflare.com/learning', link) \
					or  re.match('https://www.dta.gov.au/', link) \
					or  re.match('https://www.mozilla.org/firefox', link) \
					or  re.match('https://www.vic.gov.au/', link) \
					or  re.match('mailto:', link) \
					or  re.match(r'https?://127.0.0.1', link) \
					or  re.match(r'https?://deliver.kontent.ai', link) \
					or  re.match(r'https?://fonts.cdnfonts.com/css', link) \
					or  re.match(r'https?://localhost', link) \
					or  re.match(r'https?://manage.kontent.ai', link) \
					or  re.match(r'https?://preview-graphql.kontent.ai', link) \
					or re.search('%7B', link) \
					or re.search('example.com', link) \
					or re.search('example.org', link) \
					or re.search('file-name', link) \
					or re.search('file_name', link) \
					or re.search('filename', link) \
					or re.search(r'auth/login\?returnTo', link) \
					or re.search(r'learn/pdf/\?url', link) \
					or re.search(r'woff2?$', link) \
				   ):

				# Avoid loading the same links again and again (e.g., headers, navigation, footers, ...)
				# If the link leads to KAI Learn AND has been checked before AND was NOK, 
				# 	let's check it again because the KL web app is unreliable and one fail doesn't mean the page is truly down.
				# 	The control var for this check is shallWeCheckThis (because you can't base a condition on an array element if you're not sure the el. exists).
				# We also need a DB of links that are checked multiple times within the current page (nokLinkMultiCheck) to avoid reporting one bad link multiple times in a single page

				# Default value
				shallWeCheckThis = "nope"

				# We checked this link before
				if link in checkedLinks:
					# The link is a KL link
					if re.match('https://kontent.ai/learn', link):
						if checkedLinks[link]['status'] == "OK":
							okNormalLinks += 1
						else:
							shallWeCheckThis = "VShellPower"

					# The link is an external link
					else:
						if checkedLinks[link]['status'] == "OK":
							okNormalLinks += 1
						else:
							unreachable += 1

				# We didn't see this link before
				else:
					shallWeCheckThis = "VShellPower"
					
					# Initialize the entry for the current link in the checked links DB
					checkedLinks[link] = {}

				if shallWeCheckThis == "VShellPower":
					# Try to get the link and its response code
					# The except block is for the portals that block direct HTTP requests
					try:
						req = sessionForRequests.get(link, timeout=10)
						if re.match('https://kontent.ai/learn', link):
							time.sleep(2)
						checkedLinks[link]['code'] = req.status_code
						if req.status_code < 400:
							checkedLinks[link]['status'] = "OK"
							okNormalLinks += 1
						else:
							checkedLinks[link]['status'] = "NOK"
							unreachable += 1
							if link not in nokLinkMultiCheck:
								nokLinkMultiCheck.append(link)
								firstError = printNOK(page, link, firstError, "normalLinkUnreachable", "", checkedLinks[link]['code'])
					except:
						checkedLinks[link]['status'] = "NOK"
						checkedLinks[link]['code'] = "unknown" # We don't know the code because the request got blocked altogether
						unreachable += 1
						if link not in nokLinkMultiCheck:
							nokLinkMultiCheck.append(link)
							firstError = printNOK(page, link, firstError, "normalLinkUnresolved", "", checkedLinks[link]['code'])
				else:
					# Getting into this branch means that the link's already been checked and it was either OK, or it's an external NOK.
					# Stats counting has been done in the control variable-setting condition block -> we can pass this branch.
					pass

		if beVerbose:
			debugTime = printDebugTime("Processing normal links for " + page + " took", debugTime, startTime)		

		# Counter of the number of checked pages (This should, of course, be same as len(pagesLinksAndAnchors), but just to be sure...).
		pagesChecked += 1

	# Finished, close temporary headless Firefox and print statistics.
	finishAndQuit(exitCode, browser)
	
except Exception:
	print("Error occured or interuption code (^C) caught, terminating now.")
	exitCode = 1
	print("The exception:")
	traceback.print_exc()
	print("Last processed page URL from the sitemap:")
	print(URL)
	if page:
		print("Last processed page URL in the main DB:")
		print(page)
	if link:
		print("Last processed anchor or normal link:")
		print(link)
	finishAndQuit(exitCode, browser)
