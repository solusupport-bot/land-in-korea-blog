/* Shared regression-sensitive planner rules. No secrets or booking API. */
(()=>{const hotelTargets={seoul:{path:'/hotels/seoul-hotels-list-274/',label:'Seoul'},busan:{path:'/hotels/busan-hotels-list-253/',label:'Busan'},jeju:{path:'/hotels/jeju-hotels-list-737/',label:'Jeju City'},seogwipo:{path:'/hotels/seogwipo-hotels-list-35796/',label:'Seogwipo'}};
function normalizedPath(points,destinationLongitude){if(!points.length)return [];const shift=points[points.length-1][1]-destinationLongitude;return points.map(([lat,lon])=>[lat,lon-shift]);}
function worldBounds(points){const east=points.some(p=>p[1]>180);return [[-55,east?0:-180],[75,east?360:180]];}
function hotelDestination(city,area){const key=city==='jeju'&&['seogwipo','jungmun'].includes(area)?'seogwipo':city;if(!hotelTargets[key])throw Error('Unknown destination');return hotelTargets[key];}
function hotelLink(template,city,area){const source=new URL(template);if(source.origin!=='https://www.trip.com')throw Error('Untrusted booking origin');const target=hotelDestination(city,area);const url=new URL(target.path,source.origin);for(const key of ['Allianceid','SID','trip_sub1','trip_sub3'])if(source.searchParams.has(key))url.searchParams.set(key,source.searchParams.get(key));return url.href;}
window.LIK_PLANNER_CORE={normalizedPath,worldBounds,hotelDestination,hotelLink};})();
