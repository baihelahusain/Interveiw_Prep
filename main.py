import streamlit as st
import yaml
import requests
import time
import os
from urllib.parse import urlparse, parse_qs

# Import Google's Generative AI library for Gemini models
import google.generativeai as genai
import yt_dlp

# Determine the correct path to config.yaml
# First check in the config directory, if not found, check the current directory
config_paths = ["config/config.yaml", "config.yaml"]
config_file = None

for path in config_paths:
    if os.path.exists(path):
        config_file = path
        break

if not config_file:
    st.error("Configuration file not found. Please ensure config.yaml exists in either the current directory or a 'config' subdirectory.")
    st.stop()

# Load configuration from config.yaml
with open(config_file, "r") as f:
    config = yaml.safe_load(f)

# Set API keys from config
GEMINI_API_KEY = config["apis"]["google"]["api_key"]
YOUTUBE_API_KEY = config["apis"]["youtube"]["api_key"]
GITHUB_API_URL = config["sources"]["github_api_url"]

# Configure the Gemini API
genai.configure(api_key=GEMINI_API_KEY)

st.set_page_config(page_title=config["app"]["title"], layout="wide")
st.title(config["app"]["title"])
st.write(config["app"]["description"])

# Custom CSS for better thumbnails display
st.markdown("""
<style>
    .video-container {
        display: flex;
        align-items: center;
        margin-bottom: 20px;
    }
    .thumbnail {
        margin-right: 20px;
        border-radius: 5px;
    }
    .video-info {
        flex: 1;
    }
</style>
""", unsafe_allow_html=True)

# Sidebar for user inputs
with st.sidebar:
    st.header("Search Parameters")
    company_name = st.text_input("Company Name", placeholder="Enter company name")
    job_role = st.text_input("Job Role (optional)", placeholder="Enter job role if applicable")
    research_button = st.button("Research")

###############################################################################
# Section 1: Company Overview using Gemini model
###############################################################################
def generate_company_overview(company, role):
    # First prompt to validate if the company exists and is well-known
    validation_prompt = f"Does the company '{company}' exist as a known business entity? Respond with 'yes' or 'no'."
    
    try:
        # Validate if company exists
        model = genai.GenerativeModel("gemini-2.0-flash")
        validation_response = model.generate_content(validation_prompt)
        company_exists = "yes" in validation_response.text.lower()
        
        # If company doesn't seem to exist or is very obscure, provide appropriate message
        if not company_exists:
            return f"Sorry, I couldn't find reliable information about '{company}'. Please verify the company name or try a different company."
        
        # Build the prompt for company overview
        prompt = (
            f"Generate a detailed overview for the company '{company}'. Include details such as its foundation, "
            "CEO, services, and the countries where its products or services are available. "
            "If you're not confident about specific information, DO NOT include guesses or placeholders. "
            "Only include verified facts about the company. If you can't find enough information about this company, "
            "state clearly what information is available and what isn't."
        )
        
        if role:
            prompt += (
                f"\n\nAlso, include specific information about '{role}' positions at {company}, such as: "
                f"1. Typical job responsibilities for {role} at {company}, "
                f"2. Required skills and qualifications for this role, "
                f"3. Career growth opportunities for {role} positions, "
                f"4. Any specific technologies or tools used by {role}s at {company}."
                f"\n\nIf you don't have specific information about this role at {company}, clearly state that."
            )
            
        # Generate the company overview
        response = model.generate_content(prompt)
        overview = response.text
        
        # Check if the response contains uncertainty indicators
        uncertainty_phrases = ["I don't have", "I cannot", "I'm not able", "insufficient information"]
        if any(phrase in overview.lower() for phrase in uncertainty_phrases):
            # Content indicates uncertainty - wrap it appropriately
            return f"Based on available information about '{company}':\n\n{overview}"
        
        return overview
        
    except Exception as e:
        return f"Sorry, I couldn't generate information about '{company}' at this time. Error: {e}"

###############################################################################
# Section 2: YouTube Videos Aggregation
###############################################################################
def get_video_id(youtube_url):
    """Extract video ID from YouTube URL"""
    parsed_url = urlparse(youtube_url)
    if parsed_url.hostname in ('youtu.be',):
        return parsed_url.path[1:]
    if parsed_url.hostname in ('www.youtube.com', 'youtube.com'):
        if parsed_url.path == '/watch':
            return parse_qs(parsed_url.query)['v'][0]
        if parsed_url.path.startswith('/embed/'):
            return parsed_url.path.split('/')[2]
        if parsed_url.path.startswith('/v/'):
            return parsed_url.path.split('/')[2]
    return None

def search_youtube_videos(company, topic, max_results=3):
    """
    Scrape YouTube search results using yt_dlp.
    Returns up to max_results number of videos (default: 3).
    """
    query = f"{company} {topic}"
    ydl_opts = {
        'quiet': True,
        'noplaylist': True,
        'extract_flat': True,
        'default_search': 'ytsearch',
        'max_downloads': max_results
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            search_results = ydl.extract_info(f"ytsearch{max_results}:{query}", download=False)
            
        videos = []
        if 'entries' in search_results:
            for entry in search_results['entries']:
                # Only add videos with title and ID
                if entry.get('title') and entry.get('id'):
                    video_id = entry['id']
                    videos.append({
                        "title": entry['title'],
                        "video_url": f"https://www.youtube.com/watch?v={video_id}",
                        "thumbnail_url": f"https://img.youtube.com/vi/{video_id}/mqdefault.jpg"
                    })
        
        return videos
    except Exception as e:
        st.error(f"Error searching YouTube videos: {str(e)}")
        return []

def display_video_with_thumbnail(video):
    """Display a video with its thumbnail in a nice format"""
    st.markdown(f"""
    <div class="video-container">
        <a href="{video['video_url']}" target="_blank">
            <img class="thumbnail" src="{video['thumbnail_url']}" width="220">
        </a>
        <div class="video-info">
            <a href="{video['video_url']}" target="_blank"><strong>{video['title']}</strong></a>
        </div>
    </div>
    """, unsafe_allow_html=True)

###############################################################################
# Section 3: GitHub Resources
###############################################################################
def is_english(text):
    """
    Returns True if the given text appears to be in English (simple ASCII check).
    """
    try:
        text.encode("ascii")
    except UnicodeEncodeError:
        return False
    return True

def get_github_resources(company, job_role=None, max_results=8):
    """
    Fetch GitHub repositories focused on interview preparation and job materials 
    specifically relevant to the given company and job role.
    """
    # Create multiple targeted queries to improve results
    company_lower = company.lower()
    
    # Base queries focused on interview preparation
    queries = [
        f'"{company}" interview questions',
        f'{company} "technical interview"',
        f'{company} "coding interview"',
        f'{company} "interview preparation"',
        f'{company} "interview experience"'
    ]
    
    # Add job role specific queries if provided
    if job_role and job_role.strip():
        job_role = job_role.strip().lower()
        role_queries = [
            f'"{company}" {job_role} interview',
            f'{company} {job_role} "interview questions"',
            f'{company} {job_role} "technical interview"',
            f'{job_role} "interview preparation" {company}'
        ]
        # Add role-specific queries to the beginning for higher priority
        queries = role_queries + queries
    
    all_results = []
    
    # Try each query to collect a variety of resources
    for query in queries:
        search_url = f"{GITHUB_API_URL}/search/repositories"
        params = {
            "q": query,
            "sort": "stars",
            "order": "desc",
            "per_page": 10
        }
        
        try:
            response = requests.get(search_url, params=params, timeout=5)
            if response.status_code == 200:
                data = response.json()
                all_results.extend(data.get("items", []))
                # Short delay to avoid rate limiting
                time.sleep(0.2)
        except Exception:
            continue
    
    # If we couldn't get results with any query, return empty list
    if not all_results:
        return []
    
    # Keywords that strongly indicate a resource is specifically for interview preparation
    interview_keywords = [
        'interview question', 'hiring process', 'coding challenge', 
        'assessment', 'interview experience', 'interview prep',
        'technical interview', 'onsite interview', 'online assessment',
        'leetcode', 'interview problem'
    ]
    
    # Add job role specific keywords if provided
    if job_role and job_role.strip():
        job_role = job_role.strip().lower()
        role_keywords = [job_role, job_role.replace(' ', '')]
        interview_keywords.extend(role_keywords)
    
    # Process and deduplicate results
    seen_urls = set()
    company_resources = []
    
    for repo in all_results:
        # Skip if we've already seen this repo
        repo_url = repo.get("html_url")
        if not repo_url or repo_url in seen_urls:
            continue
        
        name = repo.get("name", "").lower()
        description = (repo.get("description") or "").lower()
        content = f"{name} {description}"
        
        # Check for company name in content to ensure relevance
        if company_lower not in content:
            continue
        
        # Prioritize repositories that match job role if provided
        is_role_specific = False
        if job_role and job_role.strip():
            is_role_specific = any(role_term in content for role_term in job_role.lower().split())
        
        # Only add to results if it's highly relevant for interview preparation
        if any(keyword in content for keyword in interview_keywords):
            resource = {
                "name": repo["full_name"],
                "url": repo_url,
                "role_specific": is_role_specific
            }
            company_resources.append(resource)
            seen_urls.add(repo_url)
            
        # Stop once we have enough results
        if len(company_resources) >= max_results * 2:  # Get more than needed so we can prioritize
            break
    
    # Prioritize role-specific resources if job role was provided
    if job_role and job_role.strip():
        # Sort putting role-specific resources first
        company_resources.sort(key=lambda x: not x.get("role_specific", False))
    
    # Remove the role_specific field before returning
    for resource in company_resources:
        if "role_specific" in resource:
            del resource["role_specific"]
    
    # If we found some resources, return them
    if company_resources:
        return company_resources[:max_results]
    
    # If we couldn't find good matches, return empty list
    return []

def get_improved_fallback_resources(company, job_role=None):
    """
    Provides curated fallback resources for popular companies when specific company resources aren't found.
    Only returns resources for specifically mapped companies, otherwise returns empty list.
    If job_role is specified, it will try to return role-specific resources for that company.
    """
    # Map popular companies to their interview preparation resources
    company_resources = {
        "amazon": [
            {"name": "Amazon Interview Guide", "url": "https://github.com/jwasham/coding-interview-university"},
            {"name": "Amazon Assessment Questions", "url": "https://github.com/twowaits/SDE-Interview-Questions/tree/master/Amazon"},
            {"name": "Amazon Interview Questions", "url": "https://github.com/krishnadey30/LeetCode-Questions-CompanyWise/blob/master/amazon_alltime.txt"}
        ],
        "google": [
            {"name": "Google Interview Questions", "url": "https://github.com/mgechev/google-interview-preparation-problems"},
            {"name": "Google Tech Dev Guide", "url": "https://github.com/jayshah19949596/CodingInterviews"},
            {"name": "Google Interview Resources", "url": "https://github.com/krishnadey30/LeetCode-Questions-CompanyWise/blob/master/google_alltime.txt"}
        ],
        "facebook": [
            {"name": "Meta/Facebook Interview Questions", "url": "https://github.com/twowaits/SDE-Interview-Questions/tree/master/Facebook"},
            {"name": "Meta Technical Interview Guide", "url": "https://github.com/khanhnamle1994/cracking-the-data-science-interview"},
            {"name": "Facebook Interview Resources", "url": "https://github.com/krishnadey30/LeetCode-Questions-CompanyWise/blob/master/facebook_alltime.txt"}
        ],
        "meta": [
            {"name": "Meta/Facebook Interview Questions", "url": "https://github.com/twowaits/SDE-Interview-Questions/tree/master/Facebook"},
            {"name": "Meta Technical Interview Guide", "url": "https://github.com/khanhnamle1994/cracking-the-data-science-interview"},
            {"name": "Facebook Interview Resources", "url": "https://github.com/krishnadey30/LeetCode-Questions-CompanyWise/blob/master/facebook_alltime.txt"}
        ],
        "microsoft": [
            {"name": "Microsoft Interview Questions", "url": "https://github.com/twowaits/SDE-Interview-Questions/tree/master/Microsoft"},
            {"name": "Microsoft Interview Preparation", "url": "https://github.com/Olshansk/interview"},
            {"name": "Microsoft Interview Resources", "url": "https://github.com/krishnadey30/LeetCode-Questions-CompanyWise/blob/master/microsoft_alltime.txt"}
        ],
        "apple": [
            {"name": "Apple Interview Preparation", "url": "https://github.com/hxu296/leetcode-company-wise-problems-2022"},
            {"name": "Apple Interview Questions", "url": "https://github.com/krishnadey30/LeetCode-Questions-CompanyWise/blob/master/apple_alltime.txt"},
            {"name": "Apple Technical Interview", "url": "https://github.com/checkcheckzz/system-design-interview"}
        ],
        "netflix": [
            {"name": "Netflix Interview Questions", "url": "https://github.com/twowaits/SDE-Interview-Questions"},
            {"name": "Netflix Technical Interview", "url": "https://github.com/yangshun/tech-interview-handbook"}
        ],
        "tesla": [
            {"name": "Tesla Interview Prep", "url": "https://github.com/krishnadey30/LeetCode-Questions-CompanyWise"},
            {"name": "Tesla Technical Questions", "url": "https://github.com/h5bp/Front-end-Developer-Interview-Questions"}
        ]
    }
    
    # Role-specific resources for common job roles
    role_resources = {
        "software engineer": [
            {"name": "Software Engineering Interview Preparation", "url": "https://github.com/jwasham/coding-interview-university"},
            {"name": "Software Engineer Coding Questions", "url": "https://github.com/twowaits/SDE-Interview-Questions"}
        ],
        "frontend": [
            {"name": "Frontend Interview Questions", "url": "https://github.com/h5bp/Front-end-Developer-Interview-Questions"},
            {"name": "Frontend Interview Handbook", "url": "https://github.com/yangshun/front-end-interview-handbook"}
        ],
        "backend": [
            {"name": "Backend Interview Questions", "url": "https://github.com/arialdomartini/Back-End-Developer-Interview-Questions"},
            {"name": "System Design for Backend Engineers", "url": "https://github.com/donnemartin/system-design-primer"}
        ],
        "data scientist": [
            {"name": "Data Science Interview Resources", "url": "https://github.com/khanhnamle1994/cracking-the-data-science-interview"},
            {"name": "Data Science Interview Questions", "url": "https://github.com/alexeygrigorev/data-science-interviews"}
        ],
        "machine learning": [
            {"name": "Machine Learning Interviews", "url": "https://github.com/chiphuyen/machine-learning-systems-design"},
            {"name": "ML Interview Guide", "url": "https://github.com/khangich/machine-learning-interview"}
        ],
        "devops": [
            {"name": "DevOps Interview Questions", "url": "https://github.com/bregman-arie/devops-exercises"},
            {"name": "DevOps Resource Collection", "url": "https://github.com/MichaelCade/90DaysOfDevOps"}
        ]
    }
    
    # Check if we have resources for this specific company
    company_lower = company.lower()
    company_specific = []
    
    for key in company_resources:
        if key in company_lower or company_lower in key:
            company_specific = company_resources[key]
            break
    
    # If job role is provided, try to find role-specific resources
    if job_role and company_specific:
        job_role_lower = job_role.lower()
        
        # Check if the job role matches any of our predefined roles
        role_specific = []
        for role_key in role_resources:
            if role_key in job_role_lower or any(term in job_role_lower for term in role_key.split()):
                role_specific = role_resources[role_key]
                break
        
        # If we found role-specific resources, combine them with company resources
        if role_specific:
            # Create a combined list with role-specific resources first
            return role_specific + company_specific
    
    # Return company-specific resources or empty list
    return company_specific

# Function to display recommended interview and career resources
def display_recommended_resources():
    st.markdown("""
        ### Recommended Job Preparation and Career Resources
        
        Here are some excellent general resources to help you prepare for tech interviews and advance your career:
        
        | Repository | Description |
        | --- | --- |
        | [Tech Interview Handbook](https://github.com/yangshun/tech-interview-handbook) | Curated coding interview preparation materials |
        | [System Design Primer](https://github.com/donnemartin/system-design-primer) | Learn how to design large-scale systems |
        | [Coding Interview University](https://github.com/jwasham/coding-interview-university) | A complete computer science study plan |
        | [Front-end Interview Questions](https://github.com/h5bp/Front-end-Developer-Interview-Questions) | Questions for front-end developer interviews |
        | [Back-end Interview Questions](https://github.com/arialdomartini/Back-End-Developer-Interview-Questions) | Questions for back-end developer interviews |
        """)

###############################################################################
# Main: Execute sections when Research button is clicked
###############################################################################
if research_button:
    if not company_name:
        st.error("Please enter a company name.")
    else:
        # Section 1: Company Overview
        st.subheader("Company Overview")
        overview = generate_company_overview(company_name, job_role)
        st.write(overview)

        # Section 2: YouTube Videos
        st.subheader("YouTube Videos")
        topics = [
            "company overview",
            "roadmap to get a job",
            "interview preparation",
            "employee experience",
            "interview questions"
        ]
        
        for topic in topics:
            st.write(f"**{topic.capitalize()}**")
            videos = search_youtube_videos(company_name, topic)
            
            if videos:
                for video in videos:
                    # Use the new display function with thumbnails
                    display_video_with_thumbnail(video)
                    st.markdown("---")
            else:
                st.warning(f"No videos found for '{topic}'.")
        
        # Add job role specific videos section if job role is provided
        if job_role:
            st.write(f"**{job_role.title()} at {company_name}**")
            
            # Create more targeted queries specifically for job role videos
            job_videos = search_youtube_videos(f"{company_name} {job_role} position", "interview experience", 3)
            
            # Check if videos are truly relevant by looking for job role keywords in titles
            relevant_job_videos = []
            job_role_terms = job_role.lower().split()
            
            if job_videos:
                for video in job_videos:
                    video_title = video["title"].lower()
                    # Only include videos that mention both the company and job role
                    if (company_name.lower() in video_title and 
                        any(term in video_title for term in job_role_terms)):
                        relevant_job_videos.append(video)
            
            if relevant_job_videos:
                st.write(f"Videos specific to {job_role} positions at {company_name}:")
                for video in relevant_job_videos:
                    display_video_with_thumbnail(video)
                    st.markdown("---")
            else:
                st.warning(f"Videos are not available for {job_role} positions at {company_name}.")

        # Section 3: GitHub Resources
        st.subheader(f"{company_name} Interview Preparation Resources")
        
        # Include job role in resource search if provided
        resources = get_github_resources(company_name, job_role)
        
        if not resources:
            # Try fallback resources for well-known companies
            resources = get_improved_fallback_resources(company_name, job_role)
            
        if resources:
            resource_description = f"for {job_role} positions at " if job_role else "for "
            st.write(f"Relevant repository links for interview preparation {resource_description}{company_name}:")
            for res in resources:
                st.markdown(f"- [{res['name']}]({res['url']})")
            
            st.info(f"These resources can help you prepare for technical interviews and the hiring process at {company_name}.")
        else:
            st.warning(f"Resources are not available for {company_name}. Please try a different company name or check the general resources below.")
            
        # Display recommended interview resources
        display_recommended_resources()
